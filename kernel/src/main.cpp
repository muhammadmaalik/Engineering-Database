#include <boost/interprocess/shared_memory_object.hpp>
#include <iostream>
#include <string>
#include <cstdlib>
#include <filesystem>

#include "vault/vault_manager.hpp"

namespace bip = boost::interprocess;

int main() {
    std::cout << "=== Motherbrain Kernel v0.2.0 ===" << std::endl;
    std::cout << "[INIT] Booting..." << std::endl;

    // --- Shared Memory IPC ---
    constexpr std::string_view shm_name = "motherbrain_ipc";

    try {
        bip::shared_memory_object shm(
            bip::open_or_create,
            shm_name.data(),
            bip::read_write
        );
        shm.truncate(1024 * 1024);
        std::cout << "[IPC] Shared memory segment '" << shm_name << "' ready (1 MB)" << std::endl;
    } catch (const bip::interprocess_exception& e) {
        std::cerr << "[FATAL] IPC Error: " << e.what() << std::endl;
        return 1;
    }

    // --- Vault Manager ---
    const char* vault_path = std::getenv("MOTHERBRAIN_VAULT");
    std::filesystem::path vault_root = vault_path 
        ? std::filesystem::path(vault_path) 
        : std::filesystem::path(std::getenv("HOME")) / ".motherbrain" / "vault";

    motherbrain::vault::VaultManager vault(vault_root);

    if (!vault.initialize()) {
        std::cerr << "[FATAL] Failed to initialize vault at: " << vault_root << std::endl;
        return 1;
    }

    // --- List indexed projects ---
    auto projects = vault.list_projects();
    if (projects.empty()) {
        std::cout << "[VAULT] No projects found. Add a manifest to " 
                  << vault_root << "/projects/<your-project>/manifest.json" << std::endl;
    } else {
        std::cout << "[VAULT] Projects indexed:" << std::endl;
        for (const auto& p : projects) {
            std::cout << "  - " << p.name << " (" << p.id << ") [" << p.status << "]" << std::endl;
        }
    }

    std::cout << "[STATUS] Kernel running. Waiting for messages..." << std::endl;
    std::cout << "[SHUTDOWN] Kernel exiting cleanly." << std::endl;
    return 0;
}
