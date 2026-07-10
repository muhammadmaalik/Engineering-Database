#include <boost/interprocess/shared_memory_object.hpp>
#include <iostream>
#include <string>

namespace bip = boost::interprocess;

int main() {
    std::cout << "=== Motherbrain Kernel v0.1.0 ===" << std::endl;
    std::cout << "[INIT] Booting..." << std::endl;

    constexpr std::string_view shm_name = "motherbrain_ipc";

    try {
        // Create or open the shared memory segment
        // If it doesn't exist, create it. If it does, open it.
        bip::shared_memory_object shm(
            bip::open_or_create,
            shm_name.data(),
            bip::read_write
        );

        // Set the size of the segment (1MB for now, will grow later)
        shm.truncate(1024 * 1024);

        std::cout << "[IPC] Shared memory segment '" << shm_name << "' created (1 MB)" << std::endl;
        std::cout << "[STATUS] Kernel running. Waiting for messages..." << std::endl;

    } catch (const bip::interprocess_exception& e) {
        std::cerr << "[FATAL] IPC Error: " << e.what() << std::endl;
        return 1;
    }

    std::cout << "[SHUTDOWN] Kernel exiting cleanly." << std::endl;
    return 0;
}
