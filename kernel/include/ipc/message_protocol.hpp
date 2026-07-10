#pragma once

#include <cstdint>
#include <array>
#include <cstring>

namespace motherbrain {
namespace ipc {

constexpr std::size_t MAX_PAYLOAD_SIZE = 65535;
constexpr std::size_t HEADER_SIZE     = 13;
constexpr std::size_t MAX_MESSAGE_SIZE = HEADER_SIZE + MAX_PAYLOAD_SIZE;

enum class MessageType : uint8_t {
    COMMAND      = 0x01,
    QUERY        = 0x02,
    RESPONSE     = 0x03,
    STREAM_START = 0x04,
    STREAM_CHUNK = 0x05,
    STREAM_END   = 0x06,
    EVENT        = 0x07,
    HEARTBEAT    = 0x08,
    ERROR_MSG    = 0x09,
    LOG          = 0x0A
};

constexpr uint32_t ADDR_KERNEL    = 0x00000000;
constexpr uint32_t ADDR_SHELL     = 0x00000001;
constexpr uint32_t ADDR_AI_FAST   = 0x00000010;
constexpr uint32_t ADDR_AI_HEAVY  = 0x00000011;
constexpr uint32_t ADDR_LOGGER    = 0x00000020;
constexpr uint32_t ADDR_BROADCAST = 0xFFFFFFFF;

#pragma pack(push, 1)
struct MessageHeader {
    MessageType type;
    uint32_t    source_id;
    uint32_t    target_id;
    uint32_t    payload_length;
};
#pragma pack(pop)

static_assert(sizeof(MessageHeader) == HEADER_SIZE, "Header must be exactly 13 bytes");

struct Message {
    MessageHeader header;

    uint8_t* payload() {
        return reinterpret_cast<uint8_t*>(this) + HEADER_SIZE;
    }

    const uint8_t* payload() const {
        return reinterpret_cast<const uint8_t*>(this) + HEADER_SIZE;
    }

    std::size_t total_size() const {
        return HEADER_SIZE + header.payload_length;
    }
};

struct RingBufferControl {
    volatile uint32_t write_index;
    volatile uint32_t read_index;
    uint32_t total_slots;
    uint32_t slot_size;
    volatile uint32_t message_count;
    uint8_t reserved[44];
};

static_assert(sizeof(RingBufferControl) == 64, "Control block must be 64 bytes");

constexpr uint32_t compute_slot_count(std::size_t segment_size) {
    return (segment_size - sizeof(RingBufferControl)) / MAX_MESSAGE_SIZE;
}

inline const char* message_type_name(MessageType type) {
    switch (type) {
        case MessageType::COMMAND:      return "COMMAND";
        case MessageType::QUERY:        return "QUERY";
        case MessageType::RESPONSE:     return "RESPONSE";
        case MessageType::STREAM_START: return "STREAM_START";
        case MessageType::STREAM_CHUNK: return "STREAM_CHUNK";
        case MessageType::STREAM_END:   return "STREAM_END";
        case MessageType::EVENT:        return "EVENT";
        case MessageType::HEARTBEAT:    return "HEARTBEAT";
        case MessageType::ERROR_MSG:    return "ERROR";
        case MessageType::LOG:          return "LOG";
        default:                        return "UNKNOWN";
    }
}

} // namespace ipc
} // namespace motherbrain
