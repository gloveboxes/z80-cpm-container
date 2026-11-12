cmake_minimum_required(VERSION 3.16)
project(altair_sound_shim C)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
if(NOT WIN32)
    find_package(Threads REQUIRED)
endif()

set(MINIAUDIO_HEADER "" CACHE FILEPATH "Path to miniaudio.h")
if(NOT MINIAUDIO_HEADER)
    set(MINIAUDIO_HEADER "${CMAKE_CURRENT_BINARY_DIR}/miniaudio.h")
    file(DOWNLOAD
        https://raw.githubusercontent.com/mackron/miniaudio/0.11.26/miniaudio.h
        "${MINIAUDIO_HEADER}"
        EXPECTED_HASH SHA256=6b376c7fbb1352ba1e0e432ab66069b335ea9405e45cb6c30b97ef0f3c9c3e0e
        STATUS status)
    list(GET status 0 error)
    if(error)
        message(FATAL_ERROR "miniaudio download failed: ${status}")
    endif()
endif()
get_filename_component(MINIAUDIO_DIR "${MINIAUDIO_HEADER}" DIRECTORY)
add_executable(altair-sound sound.c)
target_include_directories(altair-sound PRIVATE "${MINIAUDIO_DIR}")
target_compile_definitions(altair-sound PRIVATE
    MA_NO_DECODING MA_NO_ENCODING MA_NO_GENERATION
    MA_NO_RESOURCE_MANAGER MA_NO_NODE_GRAPH MA_NO_ENGINE)
if(WIN32)
    target_link_libraries(altair-sound PRIVATE ws2_32)
else()
    target_link_libraries(altair-sound PRIVATE Threads::Threads ${CMAKE_DL_LIBS})
    if(NOT APPLE)
        target_link_libraries(altair-sound PRIVATE m)
    endif()
endif()
