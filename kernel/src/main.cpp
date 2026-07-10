#include "ipc/message_bus.hpp"
#include "vault/vault_manager.hpp"
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
    std::cout << "=== Motherbrain Kernel v0.3.0 ===" << std::endl;
    std::cout << "[INIT] Booting..." << std::endl;

    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    const char* vault_path = std::getenv("MOTHERBRAIN_VAULT");
    std::filesystem::path vault_root = vault_path
        ? std::filesystem::path(vault_path)
        : std::filesystem::path(std::getenv("HOME")) / ".motherbrain" / "vault";

    vault::VaultManager vault(vault_root);

    if (!vault.initialize()) {
        std::cerr << "[FATAL] Failed to initialize vault at: " << vault_root << std::endl;
        return 1;
    }

    auto projects = vault.list_projects();
    if (projects.empty()) {
        std::cout << "[VAULT] No projects found." << std::endl;
    } else {
        std::cout << "[VAULT] Projects indexed:" << std::endl;
        for (const auto& p : projects) {
            std::cout << "  - " << p.name << " (" << p.id << ") [" << p.status << "]" << std::endl;
        }
    }

    ipc::MessageBus bus("motherbrain_ipc", 1024 * 1024);
    g_bus = &bus;

    if (!bus.initialize()) {
        std::cerr << "[FATAL] Failed to initialize message bus" << std::endl;
        return 1;
    }

    bus.on_message(ipc::ADDR_BROADCAST, [&vault](const ipc::Message& msg) {
        if (msg.header.type == ipc::MessageType::LOG) {
            std::string log_msg(reinterpret_cast<const char*>(msg.payload()), msg.header.payload_length);
            std::cout << "[LOG] " << log_msg << std::endl;
        }
    });

    std::cout << "[STATUS] Kernel running. Press Ctrl+C to stop." << std::endl;

    bus.run();

    std::cout << "[SHUTDOWN] Kernel exiting cleanly." << std::endl;
    return 0;
}
