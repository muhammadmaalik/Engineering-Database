#include "ipc/message_bus.hpp"
#include <iostream>
#include <string>
#include <thread>
#include <chrono>

using namespace motherbrain::ipc;

int main() {
    std::cout << "=== Test Sender ===" << std::endl;

    MessageBus bus("motherbrain_ipc", 1024 * 1024);

    if (!bus.initialize()) {
        std::cerr << "Failed to connect to shared memory" << std::endl;
        return 1;
    }

    std::cout << "Connected. Sending test messages every 2 seconds..." << std::endl;
    std::cout << "Press Ctrl+C to stop." << std::endl;

    int count = 0;
    while (true) {
        count++;
        std::string msg = "Hello from test sender, message #" + std::to_string(count);

        if (bus.write_string(MessageType::QUERY, 0x0000F001, ADDR_KERNEL, msg)) {
            std::cout << "Sent: " << msg << std::endl;
        } else {
            std::cerr << "Failed to send (buffer full?)" << std::endl;
        }

        // Also send a heartbeat
        bus.write_string(MessageType::HEARTBEAT, 0x0000F001, ADDR_BROADCAST, "alive");

        std::this_thread::sleep_for(std::chrono::seconds(2));
    }

    return 0;
}
