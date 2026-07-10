#include "ipc/message_bus.hpp"
#include <iostream>
#include <cstring>
#include <thread>
#include <chrono>

namespace motherbrain {
namespace ipc {

MessageBus::MessageBus(const std::string& shm_name, std::size_t segment_size)
    : shm_name_(shm_name)
    , segment_size_(segment_size)
    , shm_(bip::open_or_create, shm_name.c_str(), bip::read_write) {
}

MessageBus::~MessageBus() {
    stop();
}

bool MessageBus::initialize() {
    shm_.truncate(segment_size_);
    region_ = bip::mapped_region(shm_, bip::read_write);

    control_ = static_cast<RingBufferControl*>(region_.get_address());
    data_area_ = static_cast<uint8_t*>(region_.get_address()) + sizeof(RingBufferControl);

    if (control_->total_slots == 0) {
        control_->write_index = 0;
        control_->read_index = 0;
        control_->total_slots = compute_slot_count(segment_size_);
        control_->slot_size = MAX_MESSAGE_SIZE;
        control_->message_count = 0;
        std::memset(control_->reserved, 0, sizeof(control_->reserved));

        std::cout << "[MSG_BUS] Initialized ring buffer: "
                  << control_->total_slots << " slots, "
                  << control_->slot_size << " bytes each" << std::endl;
    }

    std::cout << "[MSG_BUS] Mapped shared memory '" << shm_name_
              << "' (" << segment_size_ << " bytes)" << std::endl;
    return true;
}

Message* MessageBus::get_slot(uint32_t index) {
    return reinterpret_cast<Message*>(data_area_ + (index * MAX_MESSAGE_SIZE));
}

uint32_t MessageBus::next_index(uint32_t current) const {
    return (current + 1) % control_->total_slots;
}

bool MessageBus::write(const MessageHeader& header, const uint8_t* payload) {
    if (control_->message_count >= control_->total_slots) {
        std::cerr << "[MSG_BUS] Buffer full, dropping message" << std::endl;
        return false;
    }

    Message* slot = get_slot(control_->write_index);
    std::memcpy(&slot->header, &header, sizeof(MessageHeader));
    if (payload && header.payload_length > 0) {
        std::memcpy(slot->payload(), payload, header.payload_length);
    }

    control_->write_index = next_index(control_->write_index);
    __sync_fetch_and_add(&control_->message_count, 1);

    return true;
}

bool MessageBus::write_string(MessageType type, uint32_t source, uint32_t target, const std::string& text) {
    MessageHeader header;
    header.type = type;
    header.source_id = source;
    header.target_id = target;
    header.payload_length = text.size();

    return write(header, reinterpret_cast<const uint8_t*>(text.data()));
}

void MessageBus::on_message(uint32_t source_id, MessageHandler handler) {
    handlers_.emplace_back(source_id, std::move(handler));
}

bool MessageBus::read_message(Message& out) {
    if (control_->message_count == 0) {
        return false;
    }

    Message* slot = get_slot(control_->read_index);
    std::memcpy(&out, slot, sizeof(MessageHeader) + slot->header.payload_length);

    control_->read_index = next_index(control_->read_index);
    __sync_fetch_and_sub(&control_->message_count, 1);

    return true;
}

void MessageBus::run() {
    running_ = true;
    std::cout << "[MSG_BUS] Event loop started. Listening for messages..." << std::endl;

    Message msg;
    while (running_) {
        while (read_message(msg)) {
            for (const auto& [source_id, handler] : handlers_) {
                if (source_id == ADDR_BROADCAST || source_id == msg.header.source_id) {
                    handler(msg);
                }
            }

            std::cout << "[MSG] " << message_type_name(msg.header.type)
                      << " | src=0x" << std::hex << msg.header.source_id
                      << " -> dst=0x" << msg.header.target_id << std::dec
                      << " | payload=" << msg.header.payload_length << " bytes" << std::endl;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    std::cout << "[MSG_BUS] Event loop stopped." << std::endl;
}

void MessageBus::stop() {
    running_ = false;
}

} // namespace ipc
} // namespace motherbrain
