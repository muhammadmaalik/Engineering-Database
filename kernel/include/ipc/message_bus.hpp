#pragma once

#include "message_protocol.hpp"
#include <boost/interprocess/shared_memory_object.hpp>
#include <boost/interprocess/mapped_region.hpp>
#include <string>
#include <functional>
#include <memory>

namespace motherbrain {
namespace ipc {

namespace bip = boost::interprocess;

class MessageBus {
public:
    using MessageHandler = std::function<void(const Message&)>;

    MessageBus(const std::string& shm_name, std::size_t segment_size = 1024 * 1024);
    ~MessageBus();

    // Initialize: map the shared memory, set up the ring buffer control block
    bool initialize();

    // Write a message into the ring buffer. Returns false if buffer is full.
    bool write(const MessageHeader& header, const uint8_t* payload);

    // Convenience overload for string payloads
    bool write_string(MessageType type, uint32_t source, uint32_t target, const std::string& text);

    // Register a handler for incoming messages from a specific source
    void on_message(uint32_t source_id, MessageHandler handler);

    // Main event loop: poll for new messages, dispatch to handlers
    // Runs until stop() is called
    void run();

    // Signal the event loop to stop
    void stop();

private:
    Message* get_slot(uint32_t index);
    uint32_t next_index(uint32_t current) const;
    bool read_message(Message& out);

    std::string shm_name_;
    std::size_t segment_size_;

    bip::shared_memory_object shm_;
    bip::mapped_region region_;
    RingBufferControl* control_ = nullptr;
    uint8_t* data_area_ = nullptr;

    bool running_ = false;
    std::vector<std::pair<uint32_t, MessageHandler>> handlers_;
};

} // namespace ipc
} // namespace motherbrain
