cmake_minimum_required(VERSION 3.20)

project(
  EmuFlow
  VERSION 0.1.0
  DESCRIPTION "Source-complete open multi-FPGA emulation flow"
  LANGUAGES CXX
)

if(POLICY CMP0167)
  cmake_policy(SET CMP0167 OLD)
endif()

include(CTest)
include(ExternalProject)
include(GNUInstallDirs)

find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
find_program(EMUFLOW_MAKE_EXECUTABLE NAMES gmake make REQUIRED)

option(EMUFLOW_BUILD_YOSYS "Build the in-tree Yosys and ABC source" ON)
option(EMUFLOW_BUILD_REPART "Build the in-tree RePart source" ON)
option(
  EMUFLOW_BUILD_TLR_ROUTER
  "Build the in-tree timing-aware load-balanced system router"
  ON
)
option(
  EMUFLOW_BUILD_TDM_RATIO_OPTIMIZER
  "Build the in-tree Lagrangian/KKT TDM ratio optimizer"
  ON
)
option(
  EMUFLOW_BUILD_PIN_PLANNER
  "Build the in-tree placement-aware TDM grouping and pin planner"
  ON
)
option(EMUFLOW_BUILD_OPENROAD "Build the in-tree OpenROAD/TritonPart source" ON)
option(EMUFLOW_BUILD_OPENPARF "Build the in-tree OpenPARF source" ON)
set(
  EMUFLOW_CMAKE_PREFIX_PATH
  ""
  CACHE STRING
  "Semicolon-separated dependency prefixes forwarded to native components"
)
set(
  EMUFLOW_EXTERNAL_JOBS
  "4"
  CACHE STRING
  "Parallel jobs used by third-party builds that do not inherit a jobserver"
)
set(
  EMUFLOW_OPENPARF_PYTHON
  "${Python3_EXECUTABLE}"
  CACHE FILEPATH
  "Python interpreter containing the PyTorch used to compile OpenPARF"
)
option(
  EMUFLOW_OPENPARF_ENABLE_CUDA
  "Compile the in-tree OpenPARF PyTorch operators with CUDA"
  OFF
)
option(
  EMUFLOW_OPENPARF_BUILD_EXPERIMENTAL_ROUTER
  "Build OpenPARF's optional experimental router (requires non-open GUROBI)"
  OFF
)

set(EMUFLOW_INSTALL_ROOT "${CMAKE_BINARY_DIR}/install")
set(EMUFLOW_INSTALL_BINDIR "${EMUFLOW_INSTALL_ROOT}/${CMAKE_INSTALL_BINDIR}")
file(MAKE_DIRECTORY "${EMUFLOW_INSTALL_BINDIR}")

file(
  GLOB_RECURSE EMUFLOW_PYTHON_SOURCES
  CONFIGURE_DEPENDS
  "${CMAKE_SOURCE_DIR}/src/emuflow/*.py"
)
configure_file(
  "${CMAKE_SOURCE_DIR}/cmake/emuflow.in"
  "${CMAKE_BINARY_DIR}/emuflow"
  @ONLY
)
file(
  CHMOD "${CMAKE_BINARY_DIR}/emuflow"
  PERMISSIONS
    OWNER_READ OWNER_WRITE OWNER_EXECUTE
    GROUP_READ GROUP_EXECUTE
    WORLD_READ WORLD_EXECUTE
)
add_custom_command(
  OUTPUT "${CMAKE_BINARY_DIR}/emuflow-python.stamp"
  COMMAND
    "${CMAKE_COMMAND}" -E copy_directory
    "${CMAKE_SOURCE_DIR}/src/emuflow"
    "${EMUFLOW_INSTALL_ROOT}/lib/emuflow"
  COMMAND
    "${CMAKE_COMMAND}" -E copy
    "${CMAKE_BINARY_DIR}/emuflow"
    "${EMUFLOW_INSTALL_BINDIR}/emuflow"
  COMMAND
    "${CMAKE_COMMAND}" -E touch
    "${CMAKE_BINARY_DIR}/emuflow-python.stamp"
  DEPENDS ${EMUFLOW_PYTHON_SOURCES}
  COMMENT "Installing the in-tree EmuFlow Python control plane"
  VERBATIM
)
add_custom_target(
  emuflow_python ALL
  DEPENDS "${CMAKE_BINARY_DIR}/emuflow-python.stamp"
)

if(EMUFLOW_BUILD_TLR_ROUTER)
  add_executable(
    emuflow_tlr_router
    "${CMAKE_SOURCE_DIR}/src/native/tlr_router.cpp"
  )
  target_compile_features(emuflow_tlr_router PRIVATE cxx_std_17)
  target_compile_options(emuflow_tlr_router PRIVATE -O3)
  set_target_properties(
    emuflow_tlr_router
    PROPERTIES
      RUNTIME_OUTPUT_DIRECTORY "${EMUFLOW_INSTALL_BINDIR}"
  )
endif()

if(EMUFLOW_BUILD_TDM_RATIO_OPTIMIZER)
  add_executable(
    emuflow_tdm_ratio_optimizer
    "${CMAKE_SOURCE_DIR}/src/native/tdm_ratio_optimizer.cpp"
  )
  target_compile_features(emuflow_tdm_ratio_optimizer PRIVATE cxx_std_17)
  target_compile_options(emuflow_tdm_ratio_optimizer PRIVATE -O3)
  set_target_properties(
    emuflow_tdm_ratio_optimizer
    PROPERTIES
      RUNTIME_OUTPUT_DIRECTORY "${EMUFLOW_INSTALL_BINDIR}"
  )
endif()

if(EMUFLOW_BUILD_PIN_PLANNER)
  add_executable(
    emuflow_pin_planner
    "${CMAKE_SOURCE_DIR}/src/native/placement_aware_pin_planner.cpp"
  )
  target_compile_features(emuflow_pin_planner PRIVATE cxx_std_17)
  target_compile_options(emuflow_pin_planner PRIVATE -O3)
  set_target_properties(
    emuflow_pin_planner
    PROPERTIES
      RUNTIME_OUTPUT_DIRECTORY "${EMUFLOW_INSTALL_BINDIR}"
  )
endif()

if(EMUFLOW_BUILD_REPART)
  find_package(Boost 1.65 REQUIRED COMPONENTS system thread)
  find_package(Threads REQUIRED)

  add_executable(
    repart
    "${CMAKE_SOURCE_DIR}/third_party/repart/RePart/partitioner.cpp"
  )
  target_compile_features(repart PRIVATE cxx_std_17)
  target_compile_options(repart PRIVATE -O3)
  target_compile_definitions(repart PRIVATE NDEBUG)
  target_include_directories(
    repart
    PRIVATE "${CMAKE_SOURCE_DIR}/third_party/repart/RePart"
  )
  target_link_libraries(
    repart
    PRIVATE Boost::system Boost::thread Threads::Threads
  )
  set_target_properties(
    repart
    PROPERTIES
      RUNTIME_OUTPUT_DIRECTORY "${EMUFLOW_INSTALL_BINDIR}"
  )
endif()

if(EMUFLOW_BUILD_YOSYS)
  set(EMUFLOW_YOSYS_SOURCE_DIR "${CMAKE_SOURCE_DIR}/third_party/yosys")
  set(EMUFLOW_YOSYS_BINARY_DIR "${CMAKE_BINARY_DIR}/yosys")
  set(EMUFLOW_YOSYS_INSTALL_ROOT "${EMUFLOW_INSTALL_ROOT}")

  # GNU Make cannot represent a makefile path containing spaces. Keep the
  # user-facing build and install tree in place while giving this one upstream
  # build a stable no-space symlink on Unix hosts.
  if(
    UNIX
    AND (
      CMAKE_SOURCE_DIR MATCHES " "
      OR CMAKE_BINARY_DIR MATCHES " "
    )
  )
    string(
      MD5 EMUFLOW_YOSYS_PATH_HASH
      "${CMAKE_SOURCE_DIR};${CMAKE_BINARY_DIR}"
    )
    set(
      EMUFLOW_YOSYS_WORK_ROOT
      "/tmp/emuflow-yosys-${EMUFLOW_YOSYS_PATH_HASH}"
    )
    file(MAKE_DIRECTORY "${EMUFLOW_YOSYS_WORK_ROOT}")
    if(NOT EXISTS "${EMUFLOW_YOSYS_WORK_ROOT}/source")
      file(
        CREATE_LINK
        "${EMUFLOW_YOSYS_SOURCE_DIR}"
        "${EMUFLOW_YOSYS_WORK_ROOT}/source"
        SYMBOLIC
      )
    endif()
    if(NOT EXISTS "${EMUFLOW_YOSYS_WORK_ROOT}/install")
      file(
        CREATE_LINK
        "${EMUFLOW_INSTALL_ROOT}"
        "${EMUFLOW_YOSYS_WORK_ROOT}/install"
        SYMBOLIC
      )
    endif()
    set(EMUFLOW_YOSYS_SOURCE_DIR "${EMUFLOW_YOSYS_WORK_ROOT}/source")
    set(EMUFLOW_YOSYS_BINARY_DIR "${EMUFLOW_YOSYS_WORK_ROOT}/build")
    set(EMUFLOW_YOSYS_INSTALL_ROOT "${EMUFLOW_YOSYS_WORK_ROOT}/install")
  endif()

  ExternalProject_Add(
    yosys_native
    SOURCE_DIR "${EMUFLOW_YOSYS_SOURCE_DIR}"
    BINARY_DIR "${EMUFLOW_YOSYS_BINARY_DIR}"
    DOWNLOAD_COMMAND ""
    UPDATE_COMMAND ""
    PATCH_COMMAND ""
    CONFIGURE_COMMAND
      "${CMAKE_COMMAND}" -E make_directory "<BINARY_DIR>"
      COMMAND
      "${EMUFLOW_MAKE_EXECUTABLE}" -C "<BINARY_DIR>"
      -f "<SOURCE_DIR>/Makefile" config-gcc
      "PREFIX=${EMUFLOW_YOSYS_INSTALL_ROOT}"
      ENABLE_READLINE=0
    BUILD_COMMAND
      "${EMUFLOW_MAKE_EXECUTABLE}" -C "<BINARY_DIR>"
      -f "<SOURCE_DIR>/Makefile"
      -j "${EMUFLOW_EXTERNAL_JOBS}"
      "PREFIX=${EMUFLOW_YOSYS_INSTALL_ROOT}"
      ENABLE_READLINE=0
    INSTALL_COMMAND
      "${EMUFLOW_MAKE_EXECUTABLE}" -C "<BINARY_DIR>"
      -f "<SOURCE_DIR>/Makefile"
      "PREFIX=${EMUFLOW_YOSYS_INSTALL_ROOT}"
      ENABLE_READLINE=0
      install
    BUILD_BYPRODUCTS
      "${EMUFLOW_INSTALL_BINDIR}/yosys"
      "${EMUFLOW_INSTALL_BINDIR}/yosys-abc"
  )
endif()

if(EMUFLOW_BUILD_OPENROAD)
  ExternalProject_Add(
    openroad_native
    SOURCE_DIR "${CMAKE_SOURCE_DIR}/third_party/openroad"
    BINARY_DIR "${CMAKE_BINARY_DIR}/openroad"
    INSTALL_DIR "${EMUFLOW_INSTALL_ROOT}"
    DOWNLOAD_COMMAND ""
    UPDATE_COMMAND ""
    PATCH_COMMAND ""
    CMAKE_ARGS
      "-DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}"
      "-DCMAKE_INSTALL_PREFIX=<INSTALL_DIR>"
      "-DCMAKE_PREFIX_PATH=${EMUFLOW_CMAKE_PREFIX_PATH}"
      "-DOPENROAD_VERSION=a008522d"
      "-DENABLE_TESTS=OFF"
      "-DBUILD_PYTHON=OFF"
      "-DBUILD_MAN=OFF"
    BUILD_COMMAND
      "${CMAKE_COMMAND}" --build "<BINARY_DIR>"
      --parallel "${EMUFLOW_EXTERNAL_JOBS}"
    INSTALL_COMMAND
      "${CMAKE_COMMAND}" --build "<BINARY_DIR>"
      --target install
      --parallel "${EMUFLOW_EXTERNAL_JOBS}"
    BUILD_BYPRODUCTS "${EMUFLOW_INSTALL_BINDIR}/openroad"
  )
endif()

if(EMUFLOW_BUILD_OPENPARF)
  ExternalProject_Add(
    openparf_native
    SOURCE_DIR "${CMAKE_SOURCE_DIR}/third_party/openparf"
    BINARY_DIR "${CMAKE_BINARY_DIR}/openparf"
    INSTALL_DIR "${EMUFLOW_INSTALL_ROOT}/openparf"
    DOWNLOAD_COMMAND ""
    UPDATE_COMMAND ""
    PATCH_COMMAND ""
    CMAKE_ARGS
      "-DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}"
      "-DCMAKE_INSTALL_PREFIX=<INSTALL_DIR>"
      "-DCMAKE_PREFIX_PATH=${EMUFLOW_CMAKE_PREFIX_PATH}"
      "-DPython3_EXECUTABLE=${EMUFLOW_OPENPARF_PYTHON}"
      "-DPYTHON_EXECUTABLE=${EMUFLOW_OPENPARF_PYTHON}"
      "-DOPENPARF_ENABLE_CUDA=${EMUFLOW_OPENPARF_ENABLE_CUDA}"
      "-DBUILD_TESTING=OFF"
      "-DENABLE_ROUTER=${EMUFLOW_OPENPARF_BUILD_EXPERIMENTAL_ROUTER}"
    BUILD_COMMAND
      "${CMAKE_COMMAND}" --build "<BINARY_DIR>"
      --parallel "${EMUFLOW_EXTERNAL_JOBS}"
    INSTALL_COMMAND
      "${CMAKE_COMMAND}" --build "<BINARY_DIR>"
      --target install
      --parallel "${EMUFLOW_EXTERNAL_JOBS}"
      COMMAND
      "${CMAKE_COMMAND}" -E copy_directory
      "${CMAKE_SOURCE_DIR}/scripts/openparf/shims/hummingbird"
      "<INSTALL_DIR>/hummingbird"
  )
endif()

# The native providers are individually parallel builds with high peak memory.
# Preserve one-command behavior while avoiding three concurrent compiler farms.
if(EMUFLOW_BUILD_YOSYS AND EMUFLOW_BUILD_OPENROAD)
  add_dependencies(openroad_native yosys_native)
endif()
if(EMUFLOW_BUILD_OPENROAD AND EMUFLOW_BUILD_OPENPARF)
  add_dependencies(openparf_native openroad_native)
elseif(EMUFLOW_BUILD_YOSYS AND EMUFLOW_BUILD_OPENPARF)
  add_dependencies(openparf_native yosys_native)
endif()

if(BUILD_TESTING)
  add_test(
    NAME emuflow_python_tests
    COMMAND
      "${Python3_EXECUTABLE}" -m unittest discover
      -s "${CMAKE_SOURCE_DIR}/tests"
      -v
  )
  set_tests_properties(
    emuflow_python_tests
    PROPERTIES
      ENVIRONMENT
        "PYTHONPATH=${CMAKE_SOURCE_DIR}/src:${CMAKE_SOURCE_DIR}"
  )

  add_test(
    NAME emuflow_source_audit
    COMMAND
      "${Python3_EXECUTABLE}"
      "${CMAKE_SOURCE_DIR}/scripts/check_source_complete.py"
      "${CMAKE_SOURCE_DIR}"
  )

  if(EMUFLOW_BUILD_REPART)
    add_test(NAME repart_cli COMMAND "$<TARGET_FILE:repart>")
    set_tests_properties(repart_cli PROPERTIES WILL_FAIL TRUE)
  endif()

  if(EMUFLOW_BUILD_TLR_ROUTER)
    add_test(
      NAME emuflow_tlr_router_cli
      COMMAND "$<TARGET_FILE:emuflow_tlr_router>" --help
    )
  endif()

  if(EMUFLOW_BUILD_TDM_RATIO_OPTIMIZER)
    add_test(
      NAME emuflow_tdm_ratio_optimizer_cli
      COMMAND "$<TARGET_FILE:emuflow_tdm_ratio_optimizer>" --help
    )
  endif()

  if(EMUFLOW_BUILD_PIN_PLANNER)
    add_test(
      NAME emuflow_pin_planner_cli
      COMMAND "$<TARGET_FILE:emuflow_pin_planner>"
    )
    set_tests_properties(
      emuflow_pin_planner_cli
      PROPERTIES WILL_FAIL TRUE
    )
  endif()

  if(EMUFLOW_BUILD_YOSYS)
    add_test(
      NAME yosys_version
      COMMAND "${EMUFLOW_INSTALL_BINDIR}/yosys" -V
    )
  endif()

  if(EMUFLOW_BUILD_OPENROAD)
    add_test(
      NAME openroad_version
      COMMAND "${EMUFLOW_INSTALL_BINDIR}/openroad" -version
    )
  endif()
endif()
