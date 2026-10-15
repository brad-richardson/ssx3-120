# Validates that deps/PS2Recomp matches the deps.lock.json engine pin.
# Fails configure on a dirty or mismatched engine checkout so a build can
# never silently mix product files with the wrong runtime.
function(ssx3_check_engine_pin ENGINE_DIR)
  file(READ "${CMAKE_CURRENT_SOURCE_DIR}/deps.lock.json" _lock)
  string(JSON _want_pin GET "${_lock}" engine pin)
  if(NOT EXISTS "${ENGINE_DIR}/.git" AND NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/.git/modules/deps/PS2Recomp")
    message(FATAL_ERROR "engine submodule missing: run `git submodule update --init -- deps/PS2Recomp`")
  endif()
  execute_process(
    COMMAND git -C "${ENGINE_DIR}" rev-parse HEAD
    OUTPUT_VARIABLE _have_pin OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE _rc)
  if(NOT _rc EQUAL 0)
    message(FATAL_ERROR "cannot read engine HEAD in ${ENGINE_DIR}")
  endif()
  if(NOT _have_pin STREQUAL _want_pin)
    message(FATAL_ERROR "engine pin mismatch: have ${_have_pin}, want ${_want_pin} (deps.lock.json). "
      "Check out the pinned commit; never track the engine branch tip implicitly.")
  endif()
  execute_process(
    COMMAND git -C "${ENGINE_DIR}" status --porcelain
    OUTPUT_VARIABLE _dirty OUTPUT_STRIP_TRAILING_WHITESPACE)
  if(_dirty)
    message(FATAL_ERROR "engine checkout is dirty:\n${_dirty}")
  endif()
  message(STATUS "engine pin OK: ${_have_pin}")
endfunction()
