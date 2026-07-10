#include "ai/inference_engine.hpp"
#include <iostream>
#include <cstring>
#include <mutex>
#include <cstdio>
#include <memory>
#include <array>
#include <sstream>

namespace motherbrain {
namespace ai {

// Simple HTTP POST helper using libc + popen (no external deps)
static std::string http_post(const std::string& host, int port, const std::string& body) {
    std::string cmd = "curl -s -X POST http://" + host + ":" + std::to_string(port) + "/completion";
    cmd += " -H \"Content-Type: application/json\"";
    cmd += " -d '" + body + "' 2>/dev/null";

    std::array<char, 8192> buffer;
    std::string result;
    FILE* pipe = popen(cmd.c_str(), "r");
    if (!pipe) return "";
    
    while (fgets(buffer.data(), buffer.size(), pipe) != nullptr) {
        result += buffer.data();
    }
    pclose(pipe);
    return result;
}

InferenceEngine::InferenceEngine(const std::string& model_path, ipc::MessageBus* bus)
    : model_path_(model_path), bus_(bus) {
}

InferenceEngine::~InferenceEngine() {
    stop();
}

bool InferenceEngine::initialize() {
    std::cout << "[AI] Starting llama-server for model: " << model_path_ << std::endl;
    
    std::string cmd = "~/llama.cpp/build/bin/llama-server";
    cmd += " -m \"" + model_path_ + "\"";
    cmd += " --host 127.0.0.1 --port 8081";
    cmd += " -ngl 99";     // Offload all layers to GPU
    cmd += " -c 2048";      // Context size
    cmd += " --no-webui";   // Don't need the web UI
    cmd += " > /tmp/motherbrain_llama_server.log 2>&1 &";  // Background
    
    std::cout << "[AI] Launch command: " << cmd << std::endl;
    int ret = std::system(cmd.c_str());
    if (ret != 0) {
        std::cerr << "[AI] Failed to start llama-server (code " << ret << ")" << std::endl;
        return false;
    }
    
    // Wait for server to be ready
    std::cout << "[AI] Waiting for server to start..." << std::endl;
    for (int i = 0; i < 60; i++) {
        std::string health = http_post("127.0.0.1", 8081, "{\"prompt\":\"test\",\"n_predict\":1}");
        if (!health.empty()) {
            std::cout << "[AI] Server is ready." << std::endl;
            return true;
        }
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }
    
    std::cerr << "[AI] Server failed to start within 60 seconds" << std::endl;
    return false;
}

void InferenceEngine::start() {
    running_ = true;
    worker_thread_ = std::thread(&InferenceEngine::inference_loop, this);
    std::cout << "[AI] Inference worker started." << std::endl;
}

void InferenceEngine::stop() {
    running_ = false;
    if (worker_thread_.joinable()) {
        worker_thread_.join();
    }
    // Kill the server
    std::system("pkill -f 'llama-server.*8081' 2>/dev/null");
}

void InferenceEngine::infer(uint32_t request_id, const std::string& prompt) {
    std::lock_guard<std::mutex> lock(mutex_);
    pending_requests_.push_back({request_id, prompt});
}

void InferenceEngine::inference_loop() {
    while (running_) {
        Request req;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (pending_requests_.empty()) {
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
                continue;
            }
            req = pending_requests_.front();
            pending_requests_.erase(pending_requests_.begin());
        }

        std::cout << "[AI] Processing request #" << req.id << ": " << req.prompt << std::endl;
        
        std::string json_body = "{\"prompt\":\"" + req.prompt + "\",\"n_predict\":256,\"temperature\":0.7}";
        std::string response = http_post("127.0.0.1", 8081, json_body);
        
        // Extract content from JSON response
        std::string content = response;
        auto pos = response.find("\"content\":\"");
        if (pos != std::string::npos) {
            content = response.substr(pos + 11);
            auto end = content.find("\"");
            if (end != std::string::npos) {
                content = content.substr(0, end);
            }
        }
        
        std::cout << "[AI] Response: " << content << std::endl;

        bus_->write_string(
            ipc::MessageType::RESPONSE,
            ipc::ADDR_AI_FAST,
            req.id,
            content
        );
    }
}

std::string InferenceEngine::run_inference(const std::string& prompt) {
    std::string json_body = "{\"prompt\":\"" + prompt + "\",\"n_predict\":256,\"temperature\":0.7}";
    return http_post("127.0.0.1", 8081, json_body);
}

} // namespace ai
} // namespace motherbrain
