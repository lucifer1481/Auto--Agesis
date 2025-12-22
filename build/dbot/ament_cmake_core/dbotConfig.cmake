# generated from ament/cmake/core/templates/nameConfig.cmake.in

# prevent multiple inclusion
if(_dbot_CONFIG_INCLUDED)
  # ensure to keep the found flag the same
  if(NOT DEFINED dbot_FOUND)
    # explicitly set it to FALSE, otherwise CMake will set it to TRUE
    set(dbot_FOUND FALSE)
  elseif(NOT dbot_FOUND)
    # use separate condition to avoid uninitialized variable warning
    set(dbot_FOUND FALSE)
  endif()
  return()
endif()
set(_dbot_CONFIG_INCLUDED TRUE)

# output package information
if(NOT dbot_FIND_QUIETLY)
  message(STATUS "Found dbot: 0.0.0 (${dbot_DIR})")
endif()

# warn when using a deprecated package
if(NOT "" STREQUAL "")
  set(_msg "Package 'dbot' is deprecated")
  # append custom deprecation text if available
  if(NOT "" STREQUAL "TRUE")
    set(_msg "${_msg} ()")
  endif()
  # optionally quiet the deprecation message
  if(NOT ${dbot_DEPRECATED_QUIET})
    message(DEPRECATION "${_msg}")
  endif()
endif()

# flag package as ament-based to distinguish it after being find_package()-ed
set(dbot_FOUND_AMENT_PACKAGE TRUE)

# include all config extra files
set(_extras "")
foreach(_extra ${_extras})
  include("${dbot_DIR}/${_extra}")
endforeach()
