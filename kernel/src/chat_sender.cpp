#include "ipc/message_bus.hpp"
#include "ipc/message_protocol.hpp"
#include <iostream>
#include <string>
#include <thread>
#include <chrono>
#include <atomic>

using namespace motherbrain::ipc;

std::atomic<bool> running{true};

int main() {
    std::cout << "=== Motherbrain Chat ===" << std::endl;
    std::cout << "Type your message and press Enter. Type /quit to exit.\n" << std::endl;

    MessageBus bus("motherbrain_ipc", 1024 * 1024);
    if (!bus.initialize()) {
        std::cerr << "Failed to connect. Is the kernel running?" << std::endl;
        return 1;
    }

    // Register handler for AI responses
    bus.on_message(ADDR_AI_FAST, [](const Message& msg) {
        if (msg.header.type == MessageType::RESPONSE) {
            std::string response(reinterpret_cast<const char*>(msg.payload()), msg.header.payload_length);
            std::cout << "\n🤖 " << response << std::endl;
            std::cout << "You> " << std::flush;
        }
    });

    // Start listener in a thread
    std::thread listener([&bus]() {
        bus.run();
    });

    std::cout << "You> " << std::flush;
    std::string input;
    while (running && std::getline(std::cin, input)) {
        if (input == "/quit" || input == "/exit") {
            bus.stop();
            break;
        }
        if (!input.empty()) {
            bus.write_string(MessageType::QUERY, 0xF001, ADDR_KERNEL, input);
        }
    }

    running = false;
    if (listener.joinable()) {
        listener.join();
    }
    std::cout << "\nGoodbye." << std::endl;
    return 0;
}
