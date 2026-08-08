TARGET = iphone:clang:latest:15.0
ARCHS = arm64 arm64e
THEOS_PACKAGE_SCHEME = rootless

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = XinglanStream
XinglanStream_FILES = Entry.xm XLFrameSource.mm XLVideoServer.mm
XinglanStream_CFLAGS = -fobjc-arc -std=c++17 -Wno-error -Wno-deprecated-declarations
XinglanStream_FRAMEWORKS = Foundation UIKit IOSurface VideoToolbox CoreMedia CoreVideo QuartzCore
XinglanStream_PRIVATE_FRAMEWORKS = IOKit
XinglanStream_LIBRARIES = substrate
XinglanStream_CODESIGN_FLAGS = -SEntitlements.plist

include $(THEOS_MAKE_PATH)/tweak.mk

