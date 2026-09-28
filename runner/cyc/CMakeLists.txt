cmake_minimum_required(VERSION 3.20)
project(nesrecomp_cyc C CXX)

# Standalone build of the pieces that need no generated code:
#   cyc_oracle       TriCNES's complete machine (the reference)
#   cyc_interp       NESRecomp's CPU on the interpreter alone, for any NROM ROM
#   cyc_helper_test  exhaustive check of the cpu6502.h operation helpers
#   cyc_fds_disk_test  FDS media parser/stream contracts (common/nes_fds.h) over
#                    fixtures written by tools/cyc/fds_fixtures.py
#   cyc_fds_board_test FDS RAM Adapter/drive contracts and a synthetic BIOS
#                    (tools/cyc/fds_board_fixtures.py) on the whole machine
#   cyc_ramview_test compiled RAM views never run stale code (builds the
#                    recompiler and a synthetic self-modifying FDS program)
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

add_executable(cyc_mapper_test mapper_test.c hw_mapper.c hw_fds.c cyc_ring.c cyc_trace.c vendor/emu2413/emu2413.c)
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
add_executable(cyc_fds_board_test fds_board_test.c cpu6502.c cpu6502_interp.c hw_machine.c hw_mapper.c hw_fds.c
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
set(NESRECOMP_RAMVIEW_BUILD --cmake ${CMAKE_COMMAND} --generator ${CMAKE_GENERATOR} --c-compiler ${CMAKE_C_COMPILER})
if(NOT CMAKE_CONFIGURATION_TYPES AND CMAKE_MAKE_PROGRAM)
    list(APPEND NESRECOMP_RAMVIEW_BUILD --make-program ${CMAKE_MAKE_PROGRAM})
endif()
add_test(NAME cyc_ramview_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/../../tools/cyc/test_cyc_ramviews.py
            --recompiler $<TARGET_FILE:NESRecomp> --interp $<TARGET_FILE:cyc_interp>
            --out ${CMAKE_CURRENT_BINARY_DIR}/ramview_test ${NESRECOMP_RAMVIEW_BUILD} --config "$<CONFIG>")
set_tests_properties(cyc_ramview_test PROPERTIES TIMEOUT 1800)
