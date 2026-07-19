from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives import serialization

from core import peer_auth


def test_identity_is_persistent_and_private_material_is_protected(tmp_path, monkeypatch):
    monkeypatch.setattr(peer_auth, "_dpapi", lambda value, protect: None)
    store = peer_auth.IdentityStore(tmp_path / "auth")
    identity = store.load_or_create_identity("laptop")

    assert store.load_identity().public_key_b64 == identity.public_key_b64
    private_bytes = identity.private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    assert private_bytes not in store.identity_path.read_bytes()
    assert store.identity_path.parent == tmp_path / "auth"


def test_pairing_requires_matching_sas_and_expires(tmp_path):
    source = peer_auth.IdentityStore(tmp_path / "source").load_or_create_identity("source")
    target = peer_auth.IdentityStore(tmp_path / "target")
    bundle = peer_auth.create_pairing_bundle(source, now=1_000)

    assert len(bundle["sas"]) == 8 and bundle["sas"].isdigit()
    with pytest.raises(peer_auth.PeerAuthError, match="SAS"):
        peer_auth.confirm_pairing(target, bundle, "00000000", now=1_001)

    peer = peer_auth.confirm_pairing(target, bundle, bundle["sas"], ("sync:read",), now=1_001)
    assert peer.device_id == source.device_id
    assert peer.scopes == ("sync:read",)
    with pytest.raises(peer_auth.PeerAuthError, match="expired"):
        peer_auth.confirm_pairing(target, bundle, bundle["sas"], now=1_121)


def test_signed_request_rejects_replay_tampering_and_missing_scope(tmp_path):
    client = peer_auth.IdentityStore(tmp_path / "client").load_or_create_identity("client")
    server = peer_auth.IdentityStore(tmp_path / "server")
    server.trust_peer(client.device_id, client.public_key_b64, scopes=("sync:read",))
    now = int(time.time())
    headers = peer_auth.sign_request(
        client, "POST", "/v2/sync/pull", "b=2&a=1", b"{}", timestamp=now, nonce="01" * 16
    )

    verified = peer_auth.verify_request(
        server, "POST", "/v2/sync/pull", "a=1&b=2", b"{}", headers, "sync:read", now=now
    )
    assert verified.device_id == client.device_id
    with pytest.raises(peer_auth.PeerAuthError, match="replayed"):
        peer_auth.verify_request(
            server, "POST", "/v2/sync/pull", "a=1&b=2", b"{}", headers, "sync:read", now=now
        )

    second = peer_auth.sign_request(
        client, "POST", "/v2/sync/push", "", b"{}", timestamp=now, nonce="02" * 16
    )
    with pytest.raises(peer_auth.PeerAuthError, match="scope"):
        peer_auth.verify_request(
            server, "POST", "/v2/sync/push", "", b"{}", second, "sync:write", now=now
        )

    tampered = peer_auth.sign_request(
        client, "POST", "/v2/sync/pull", "", b"{}", timestamp=now, nonce="03" * 16
    )
    with pytest.raises(peer_auth.PeerAuthError, match="hash"):
        peer_auth.verify_request(
            server, "POST", "/v2/sync/pull", "", b'{"changed":true}', tampered, "sync:read", now=now
        )


def test_response_signature_and_revoke(tmp_path):
    server = peer_auth.IdentityStore(tmp_path / "server").load_or_create_identity("server")
    client_store = peer_auth.IdentityStore(tmp_path / "client")
    trusted = client_store.trust_peer(server.device_id, server.public_key_b64)
    body = b'{"ok":true}'
    headers = peer_auth.sign_response(server, 200, body, "ab" * 16)

    peer_auth.verify_response(trusted, 200, body, "ab" * 16, headers)
    with pytest.raises(peer_auth.PeerAuthError):
        peer_auth.verify_response(trusted, 200, body + b" ", "ab" * 16, headers)
    assert peer_auth.revoke_peer(client_store, server.device_id)
    assert not peer_auth.revoke_peer(client_store, server.device_id)


def test_connection_key_requires_both_confirmations(tmp_path):
    host = peer_auth.IdentityStore(tmp_path / "host").load_or_create_identity("host")
    guest = peer_auth.IdentityStore(tmp_path / "guest").load_or_create_identity("guest")
    key, invitation = peer_auth.encode_connection_key(host, "http://10.0.0.1:8090", now=1_000)
    decoded = peer_auth.decode_connection_key(key, now=1_001)
    request = peer_auth.create_join_request(guest, decoded)
    sessions = peer_auth.PairingSessionStore(tmp_path / "sessions")
    sessions.open(invitation)
    joined = sessions.join(request, now=1_001)
    assert joined["sas"].isdigit() and len(joined["sas"]) == 8

    first = sessions.confirm(
        decoded["session_id"], decoded["secret"], "guest", joined["sas"], now=1_001
    )
    assert first["complete"] is False
    assert sessions.completed_guest(decoded["session_id"], decoded["secret"]) is None
    final = sessions.confirm(
        decoded["session_id"], decoded["secret"], "host", joined["sas"], now=1_001
    )
    assert final["complete"] is True
    assert sessions.completed_guest(decoded["session_id"], decoded["secret"])["guest_device_id"] == guest.device_id
