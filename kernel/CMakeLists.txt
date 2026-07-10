cmake_minimum_required(VERSION 3.20)
project(MotherbrainKernel LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Boost REQUIRED COMPONENTS system)
find_package(SQLite3 REQUIRED)
find_package(nlohmann_json REQUIRED)

# The kernel daemon
add_executable(motherbraind
    src/main.cpp
    src/vault/vault_manager.cpp
    src/ipc/message_bus.cpp
)

# Test harness for injecting messages
add_executable(test_sender
    src/test_sender.cpp
    src/ipc/message_bus.cpp
)

target_include_directories(motherbraind PRIVATE
    ${CMAKE_SOURCE_DIR}/include
    ${SQLite3_INCLUDE_DIRS}
)

target_include_directories(test_sender PRIVATE
    ${CMAKE_SOURCE_DIR}/include
)

target_link_libraries(motherbraind PRIVATE
    Boost::system
    SQLite::SQLite3
)

target_link_libraries(test_sender PRIVATE
    Boost::system
)

target_compile_options(motherbraind PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_compile_options(test_sender PRIVATE -Wall -Wextra -Wpedantic -Werror)
