#include "ipc/message_bus.hpp"
#include "ipc/message_protocol.hpp"
#include "vault/vault_manager.hpp"
#include "ai/inference_engine.hpp"
#include <iostream>
#include <string>
#include <cstdlib>
#include <filesystem>
#include <csignal>

namespace bip = boost::interprocess;
using namespace motherbrain;

ipc::MessageBus* g_bus = nullptr;

void signal_handler(int) {
    std::cout << "\n[INIT] Received shutdown signal." << std::endl;
    if (g_bus) g_bus->stop();
}

int main() {
    std::cout << "=== Motherbrain Kernel v0.5.1 ===" << std::endl;
    std::cout << "[INIT] Booting..." << std::endl;

    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    const char* vault_path = std::getenv("MOTHERBRAIN_VAULT");
    std::filesystem::path vault_root = vault_path
        ? std::filesystem::path(vault_path)
        : std::filesystem::path(std::getenv("HOME")) / ".motherbrain" / "vault";

    vault::VaultManager vault(vault_root);
    if (!vault.initialize()) {
        std::cerr << "[FATAL] Failed to initialize vault" << std::endl;
        return 1;
    }

    ipc::MessageBus bus("motherbrain_ipc", 1024 * 1024);
    g_bus = &bus;
    if (!bus.initialize()) {
        std::cerr << "[FATAL] Failed to initialize message bus" << std::endl;
        return 1;
    }

    std::filesystem::path model_path = vault_root / "shared" / "base_models" / "gemma-2-9b-it-Q5_K_M.gguf";
    ai::InferenceEngine ai_engine(model_path.string(), &bus);
    if (!ai_engine.initialize()) {
        std::cerr << "[FATAL] Failed to initialize AI engine" << std::endl;
        return 1;
    }
    ai_engine.start();

    bus.on_message(ipc::ADDR_BROADCAST, [&vault, &ai_engine](const ipc::Message& msg) {
        std::string payload(reinterpret_cast<const char*>(msg.payload()), msg.header.payload_length);
        vault.log_message(msg.header.source_id, msg.header.target_id,
            static_cast<uint8_t>(msg.header.type), ipc::message_type_name(msg.header.type), payload);

        if (msg.header.type == ipc::MessageType::QUERY) {
            std::cout << "[KERNEL] Query: " << payload << std::endl;
            ai_engine.infer(msg.header.source_id, payload);
        }
    });

    std::cout << "[STATUS] Kernel running. Press Ctrl+C to stop." << std::endl;
    bus.run();
    ai_engine.stop();
    std::cout << "[SHUTDOWN] Kernel exiting cleanly." << std::endl;
    return 0;
}
