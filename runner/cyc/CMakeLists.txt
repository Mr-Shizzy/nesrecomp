cmake_minimum_required(VERSION 3.20)
project(nesrecomp_cyc C CXX)

# Standalone build of the pieces that need no generated code:
#   cyc_oracle       TriCNES's complete machine (the reference)
#   cyc_interp       NESRecomp's CPU on the interpreter alone, for any NROM ROM
#   cyc_helper_test  exhaustive check of the cpu6502.h operation helpers
#   cyc_fds_disk_test  FDS media parser/stream contracts (common/nes_fds.h) and
#                    disk save file / Mesen .ips contracts (common/nes_fds_save.h)
#                    over fixtures written by tools/cyc/fds_fixtures.py
#   cyc_fds_saves    disk saves across processes (tools/cyc/test_cyc_fds_saves.py)
#   cyc_fds_board_test FDS RAM Adapter/drive contracts and a synthetic BIOS
#                    (tools/cyc/fds_board_fixtures.py) on the whole machine
#   cyc_ramview_test compiled RAM views never run stale code (builds the
#                    recompiler and a synthetic self-modifying FDS program)
#   cyc_fds_audio_test the FDS sound unit against an independent model and in
#                    rendered PCM (builds synthetic FDS sound programs)
# Tests that build a generated program use this build's toolchain
# (nested_build.json below, tools/cyc/nested_build.py).
# Game projects use runner/cyc/cyc.cmake instead.

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

include(${CMAKE_CURRENT_SOURCE_DIR}/cyc.cmake)

nesrecomp_cyc_add_oracle(cyc_oracle)

add_executable(cyc_interp ${NESRECOMP_CYC_SOURCES} cyc_native_none.c)
target_include_directories(cyc_interp PRIVATE ${NESRECOMP_CYC_INCLUDE_DIRS})
target_link_libraries(cyc_interp PRIVATE ${NESRECOMP_CYC_LIBRARIES})
if(MSVC)
    target_compile_definitions(cyc_interp PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()

add_executable(cyc_helper_test ${NESRECOMP_CYC_HELPER_TEST_SOURCES})
target_include_directories(cyc_helper_test PRIVATE ${NESRECOMP_CYC_INCLUDE_DIRS})
enable_testing()
add_test(NAME cyc_helper_test COMMAND cyc_helper_test)

add_executable(cyc_mapper_test mapper_test.c hw_mapper.c hw_fds.c hw_fds_audio.c cyc_ring.c cyc_trace.c vendor/emu2413/emu2413.c)
target_link_libraries(cyc_mapper_test PRIVATE ${NESRECOMP_CYC_LIBRARIES})
target_include_directories(cyc_mapper_test PRIVATE ${NESRECOMP_CYC_INCLUDE_DIRS})
add_test(NAME cyc_mapper_test COMMAND cyc_mapper_test)

add_executable(cyc_cart_header_test cart_header_test.c)
add_test(NAME cyc_cart_header_test COMMAND cyc_cart_header_test)

# FDS media: the fixture generator runs first (FIXTURES_SETUP), then the test.
find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
add_executable(cyc_fds_disk_test fds_disk_test.c)
if(MSVC)
    target_compile_definitions(cyc_fds_disk_test PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()
set(NESRECOMP_FDS_FIXTURES ${CMAKE_CURRENT_BINARY_DIR}/fds_fixtures)
add_test(NAME cyc_fds_fixtures
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/../../tools/cyc/fds_fixtures.py
            --out ${NESRECOMP_FDS_FIXTURES})
set_tests_properties(cyc_fds_fixtures PROPERTIES FIXTURES_SETUP fds_fixtures)
add_test(NAME cyc_fds_disk_test COMMAND cyc_fds_disk_test ${NESRECOMP_FDS_FIXTURES})
set_tests_properties(cyc_fds_disk_test PROPERTIES FIXTURES_REQUIRED fds_fixtures)

# FDS RAM Adapter and drive: register/IRQ/drive contracts, then a synthetic
# BIOS (tools/cyc/fds_board_fixtures.py) run on the whole machine.
add_executable(cyc_fds_board_test fds_board_test.c cpu6502.c cpu6502_interp.c hw_machine.c hw_mapper.c hw_fds.c hw_fds_audio.c
    vendor/emu2413/emu2413.c hw_ppu.c hw_apu.c hw_palette.c cyc_trace.c cyc_ring.c cyc_run.c cyc_ramview.c cyc_native_none.c)
target_include_directories(cyc_fds_board_test PRIVATE ${NESRECOMP_CYC_INCLUDE_DIRS})
target_link_libraries(cyc_fds_board_test PRIVATE ${NESRECOMP_CYC_LIBRARIES})
if(MSVC)
    target_compile_definitions(cyc_fds_board_test PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()
set(NESRECOMP_FDS_BOARD_FIXTURES ${CMAKE_CURRENT_BINARY_DIR}/fds_board_fixtures)
add_test(NAME cyc_fds_board_fixtures
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/../../tools/cyc/fds_board_fixtures.py
            --out ${NESRECOMP_FDS_BOARD_FIXTURES})
set_tests_properties(cyc_fds_board_fixtures PROPERTIES FIXTURES_SETUP fds_board_fixtures)
add_test(NAME cyc_fds_board_test COMMAND cyc_fds_board_test ${NESRECOMP_FDS_BOARD_FIXTURES})
set_tests_properties(cyc_fds_board_test PROPERTIES FIXTURES_REQUIRED fds_board_fixtures)


# Compiled views of code in RAM (FDS disk files, captured code): a synthetic
# FDS program whose RAM code rewrites itself every way a view can go stale
# (tools/cyc/fds_ramview_fixtures.py), compiled from its disk, then again with
# the capture of a run; native, --interp-only and cyc_interp must match on
# every cycle at all four alignments. Needs the recompiler, built here.
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../../recompiler ${CMAKE_CURRENT_BINARY_DIR}/recompiler)

# Tests that compile a generated program build it with this build's toolchain,
# recorded here (tools/cyc/nested_build.py): cmake, generator, make program,
# compiler and tools, and for MSVC with a single-configuration generator the
# compiler environment this was configured in, so a plain ctest needs no
# developer prompt. The CTest commands only name this file.
function(nesrecomp_json_string out value)
    string(REPLACE "\\" "\\\\" value "${value}")
    string(REPLACE "\"" "\\\"" value "${value}")
    set(${out} "\"${value}\"" PARENT_SCOPE)
endfunction()
set(NESRECOMP_NESTED_BUILD ${CMAKE_CURRENT_BINARY_DIR}/nested_build.json)
set(nested_fields "")
foreach(pair "cmake=${CMAKE_COMMAND}" "generator=${CMAKE_GENERATOR}" "platform=${CMAKE_GENERATOR_PLATFORM}"
             "toolset=${CMAKE_GENERATOR_TOOLSET}" "instance=${CMAKE_GENERATOR_INSTANCE}")
    string(FIND "${pair}" "=" eq)
    string(SUBSTRING "${pair}" 0 ${eq} key)
    math(EXPR eq "${eq} + 1")
    string(SUBSTRING "${pair}" ${eq} -1 value)
    nesrecomp_json_string(value "${value}")
    string(APPEND nested_fields "  \"${key}\": ${value},\n")
endforeach()
if(CMAKE_CONFIGURATION_TYPES)
    string(APPEND nested_fields "  \"multi_config\": true\n")
else()
    nesrecomp_json_string(make "${CMAKE_MAKE_PROGRAM}")
    nesrecomp_json_string(cc "${CMAKE_C_COMPILER}")
    string(APPEND nested_fields "  \"multi_config\": false,\n  \"make_program\": ${make},\n  \"c_compiler\": ${cc},\n")
    set(tools "")
    set(path_dirs "")
    foreach(pair "linker=${CMAKE_LINKER}" "rc=${CMAKE_RC_COMPILER}" "mt=${CMAKE_MT}" "ar=${CMAKE_AR}")
        string(FIND "${pair}" "=" eq)
        string(SUBSTRING "${pair}" 0 ${eq} key)
        math(EXPR eq "${eq} + 1")
        string(SUBSTRING "${pair}" ${eq} -1 value)
        if(value)
            nesrecomp_json_string(json "${value}")
            list(APPEND tools "\"${key}\": ${json}")
        endif()
    endforeach()
    list(JOIN tools ", " tools)
    string(APPEND nested_fields "  \"tools\": {${tools}},\n")
    set(env "")
    if(MSVC)
        foreach(tool "${CMAKE_C_COMPILER}" "${CMAKE_LINKER}" "${CMAKE_RC_COMPILER}" "${CMAKE_MT}")
            if(tool)
                get_filename_component(dir "${tool}" DIRECTORY)
                file(TO_NATIVE_PATH "${dir}" dir)
                nesrecomp_json_string(dir "${dir}")
                list(APPEND path_dirs "${dir}")
            endif()
        endforeach()
        list(REMOVE_DUPLICATES path_dirs)
        # Built as a string: the values are ;-separated and must not become CMake lists.
        foreach(var INCLUDE LIB LIBPATH)
            if(DEFINED ENV{${var}})
                nesrecomp_json_string(json "$ENV{${var}}")
                if(env)
                    string(APPEND env ", ")
                endif()
                string(APPEND env "\"${var}\": ${json}")
            endif()
        endforeach()
    endif()
    list(JOIN path_dirs ", " path_dirs)
    string(APPEND nested_fields "  \"env\": {${env}},\n  \"path\": [${path_dirs}]\n")
endif()
file(WRITE ${NESRECOMP_NESTED_BUILD} "{\n${nested_fields}}\n")
set(NESRECOMP_NESTED_BUILD_ARGS --toolchain ${NESRECOMP_NESTED_BUILD} --config "$<CONFIG>")

add_test(NAME cyc_ramview_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/../../tools/cyc/test_cyc_ramviews.py
            --recompiler $<TARGET_FILE:NESRecomp> --interp $<TARGET_FILE:cyc_interp>
            --out ${CMAKE_CURRENT_BINARY_DIR}/ramview_test ${NESRECOMP_NESTED_BUILD_ARGS})
set_tests_properties(cyc_ramview_test PROPERTIES TIMEOUT 1800)

# FDS disk saves across processes: a synthetic program that saves to its disk
# (tools/cyc/fds_save_fixtures.py) run by cyc_interp at all four alignments:
# write -> save file -> reload round trips, the image never modified, foreign /
# corrupt / newer saves refused and left intact, save cadence, write protect,
# Mesen .ips export/import and the write position.
add_test(NAME cyc_fds_saves
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/../../tools/cyc/test_cyc_fds_saves.py
            --host $<TARGET_FILE:cyc_interp> --out ${CMAKE_CURRENT_BINARY_DIR}/fds_saves)
set_tests_properties(cyc_fds_saves PROPERTIES TIMEOUT 900)
# The FDS sound unit (hw_fds_audio.c): synthetic FDS programs that exercise
# every register and behaviour the CPU can observe, compiled from their disks;
# native, --interp-only and cyc_interp must match on every cycle at all four
# alignments, every sound register read and every frame's sound state must
# match an independent model of Mesen's FdsAudio (tools/cyc/fds_audio_model.py),
# and the rendered PCM must have the expected pitch and FDS/2A03 level.
add_test(NAME cyc_fds_audio_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/../../tools/cyc/test_cyc_fds_audio.py
            --recompiler $<TARGET_FILE:NESRecomp> --interp $<TARGET_FILE:cyc_interp>
            --out ${CMAKE_CURRENT_BINARY_DIR}/fds_audio_test ${NESRECOMP_NESTED_BUILD_ARGS})
set_tests_properties(cyc_fds_audio_test PROPERTIES TIMEOUT 1800)
