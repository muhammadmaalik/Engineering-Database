#pragma once

#include "ipc/message_bus.hpp"
#include <string>
#include <thread>
#include <atomic>
#include <functional>
#include <vector>
#include <mutex>

namespace motherbrain {
namespace ai {

class InferenceEngine {
public:
    InferenceEngine(const std::string& model_path, ipc::MessageBus* bus);
    ~InferenceEngine();

    bool initialize();
    void start();
    void stop();
    void infer(uint32_t request_id, const std::string& prompt);

private:
    void inference_loop();
    std::string run_inference(const std::string& prompt);

    std::string model_path_;
    ipc::MessageBus* bus_;

    std::thread worker_thread_;
    std::atomic<bool> running_{false};

    struct Request {
        uint32_t id;
        std::string prompt;
    };
    std::vector<Request> pending_requests_;
    std::mutex mutex_;
};

} // namespace ai
} // namespace motherbrain
