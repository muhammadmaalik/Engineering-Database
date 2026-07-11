#include <iostream>
#include <cstdio>
#include <string>
#include <array>

namespace motherbrain {
namespace ai {

std::string execute_tool(const std::string& tool_name, const std::string& args) {
    std::string cmd = "python ~/motherbrain/tools/system_agent.py " + tool_name + " " + args + " 2>&1";
    
    std::array<char, 8192> buffer;
    std::string result;
    FILE* pipe = popen(cmd.c_str(), "r");
    if (!pipe) return "Error: Failed to execute tool";
    
    while (fgets(buffer.data(), buffer.size(), pipe) != nullptr) {
        result += buffer.data();
    }
    pclose(pipe);
    return result;
}

} // namespace ai
} // namespace motherbrain
