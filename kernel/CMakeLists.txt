cmake_minimum_required(VERSION 3.20)
project(MotherbrainKernel LANGUAGES CXX)

# C++20 is non-negotiable
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Find Boost libraries we need
find_package(Boost REQUIRED COMPONENTS system)

# Our kernel source files
add_executable(motherbraind
    src/main.cpp
)

# Link Boost
target_link_libraries(motherbraind PRIVATE Boost::system)

# Compiler warnings as errors - we write clean code
target_compile_options(motherbraind PRIVATE -Wall -Wextra -Wpedantic -Werror)
