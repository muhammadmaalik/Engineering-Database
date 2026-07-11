#include "ai/inference_engine.hpp"
#include <iostream>
#include <cstring>
#include <mutex>
#include <cstdio>
#include <memory>
#include <array>
#include <sstream>
#include <fstream>

namespace motherbrain {
namespace ai {

static std::string http_post(const std::string& host, int port, const std::string& body) {
    // Write body to temp file to avoid shell escaping issues
    std::string tmpfile = "/tmp/motherbrain_http_body.json";
    std::ofstream out(tmpfile);
    out << body;
    out.close();
    
    std::string cmd = "curl -s -X POST http://" + host + ":" + std::to_string(port) + "/chat/completions";
    cmd += " -H \"Content-Type: application/json\"";
    cmd += " -d @" + tmpfile + " 2>/dev/null";

    std::array<char, 16384> buffer;
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
    cmd += " -ngl 99";
    cmd += " -c 2048";
    cmd += " > /tmp/motherbrain_llama_server.log 2>&1 &";
    
    int ret = std::system(cmd.c_str());
    if (ret != 0) {
        std::cerr << "[AI] Failed to start llama-server (code " << ret << ")" << std::endl;
        return false;
    }
    
    std::cout << "[AI] Waiting for server to start..." << std::endl;
    for (int i = 0; i < 60; i++) {
        std::string test = "{\"messages\":[{\"role\":\"user\",\"content\":\"test\"}],\"max_tokens\":1}";
        std::string health = http_post("127.0.0.1", 8081, test);
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
    std::system("pkill -f 'llama-server.*8081' 2>/dev/null");
}

void InferenceEngine::infer(uint32_t request_id, const std::string& prompt) {
    std::lock_guard<std::mutex> lock(mutex_);
    pending_requests_.push_back({request_id, prompt});
}

static std::string escape_json(const std::string& s) {
    std::string out;
    for (char c : s) {
        if (c == '"') out += "\\\"";
        else if (c == '\\') out += "\\\\";
        else if (c == '\n') out += "\\n";
        else if (c == '\r') out += "\\r";
        else if (c == '\t') out += "\\t";
        else out += c;
    }
    return out;
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

        std::cout << "[AI] Processing: " << req.prompt << std::endl;
        
        std::string escaped = escape_json(req.prompt);
        
        std::string system_prompt = "You are Motherbrain, an AI with full access to the user's computer. "
            "You can read files, write files, list directories, run shell commands, and search for files. "
            "To use a tool, respond ONLY with this exact format on a single line:\\n"
            "[TOOL:tool_name] arguments\\n\\n"
            "Available tools:\\n"
            "- list_directory <path>\\n"
            "- read_file <path>\\n"
            "- write_file <path> <content>\\n"
            "- run_command <command>\\n"
            "- search_files <pattern> <directory>\\n\\n"
            "When the user asks you to do something that requires accessing their computer, "
            "respond with the appropriate [TOOL:] command. Nothing else.";
        
        std::string escaped_system = escape_json(system_prompt);
        
        std::string json_body = "{\"messages\":["
            "{\"role\":\"system\",\"content\":\"" + escaped_system + "\"},"
            "{\"role\":\"user\",\"content\":\"" + escaped + "\"}"
            "],\"max_tokens\":256,\"temperature\":0.7}";
        
        std::string response = http_post("127.0.0.1", 8081, json_body);
        
        std::string content;
        auto pos = response.find("\"content\":\"");
        if (pos != std::string::npos) {
            content = response.substr(pos + 11);
            auto end = content.find("\"");
            if (end != std::string::npos) {
                content = content.substr(0, end);
            }
        }
        
        std::string unescaped;
        for (size_t i = 0; i < content.size(); i++) {
            if (content[i] == '\\' && i+1 < content.size()) {
                if (content[i+1] == 'n') { unescaped += '\n'; i++; }
                else if (content[i+1] == 't') { unescaped += '\t'; i++; }
                else if (content[i+1] == '"') { unescaped += '"'; i++; }
                else if (content[i+1] == '\\') { unescaped += '\\'; i++; }
                else { unescaped += content[i]; }
            } else {
                unescaped += content[i];
            }
        }
        
        std::cout << "[AI] Response: " << unescaped << std::endl;

        bus_->write_string(
            ipc::MessageType::RESPONSE,
            ipc::ADDR_AI_FAST,
            req.id,
            unescaped
        );
    }
}

std::string InferenceEngine::run_inference(const std::string& prompt) {
    std::string escaped = escape_json(prompt);
    std::string json_body = "{\"messages\":[{\"role\":\"user\",\"content\":\"" + escaped + "\"}],\"max_tokens\":256,\"temperature\":0.7}";
    return http_post("127.0.0.1", 8081, json_body);
}

} // namespace ai
} // namespace motherbrain
