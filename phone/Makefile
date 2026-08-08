TARGET = iphone:clang:latest:15.0
ARCHS = arm64 arm64e
THEOS_PACKAGE_SCHEME = rootless

include $(THEOS)/makefiles/common.mk

TOOL_NAME = xlstreamd
xlstreamd_FILES = main.mm XLFrameSource.mm XLVideoServer.mm XLControlServer.mm XLStatusServer.mm XLHIDSender.mm
xlstreamd_CFLAGS = -fobjc-arc -std=c++17 -Wall -Wextra -Wno-error -Wno-deprecated-declarations
xlstreamd_FRAMEWORKS = Foundation UIKit IOSurface VideoToolbox CoreMedia CoreVideo QuartzCore IOKit
xlstreamd_CODESIGN_FLAGS = -SEntitlements.plist
xlstreamd_INSTALL_PATH = /usr/libexec

include $(THEOS_MAKE_PATH)/tool.mk
