TARGET = iphone:clang:latest:15.0
ARCHS = arm64 arm64e
THEOS_PACKAGE_SCHEME ?= roothide

include $(THEOS)/makefiles/common.mk

TOOL_NAME = XLStreamLauncher xlstreamd xltouchd
TWEAK_NAME = XLSystemActions XLTouchActions

XLStreamLauncher_FILES = XLLauncher.mm
XLStreamLauncher_CFLAGS = -fobjc-arc -Wall -Wextra -Wno-error
XLStreamLauncher_FRAMEWORKS = Foundation
XLStreamLauncher_CODESIGN_FLAGS = -SLauncherEntitlements.plist
XLStreamLauncher_INSTALL_PATH = /Applications/XLStream.app

xlstreamd_FILES = main.mm XLFrameSource.mm XLVideoServer.mm XLControlServer.mm XLStatusServer.mm XLFileTransferServer.mm XLHIDSender.mm
xlstreamd_CFLAGS = -fobjc-arc -std=c++17 -Wall -Wextra -Wno-error -Wno-deprecated-declarations -Wno-module-import-in-extern-c
xlstreamd_FRAMEWORKS = Foundation UIKit IOSurface VideoToolbox CoreMedia CoreVideo QuartzCore IOKit Security
xlstreamd_CODESIGN_FLAGS = -SEntitlements.plist
xlstreamd_INSTALL_PATH = /Applications/XLStream.app/bin

xltouchd_FILES = vendor/ioscpy/daemon/main.mm vendor/ioscpy/daemon/ControlServer.mm vendor/ioscpy/daemon/FrameStore.mm vendor/ioscpy/daemon/FrameIngest.mm vendor/ioscpy/daemon/Protocol.mm vendor/ioscpy/jbcompat/Detect.mm vendor/ioscpy/jbcompat/Paths.mm
xltouchd_CFLAGS = -fobjc-arc -Ivendor/ioscpy/daemon -Ivendor/ioscpy/jbcompat -Wall -Wextra -Wno-error -Wno-deprecated-declarations
xltouchd_FRAMEWORKS = Foundation
xltouchd_CODESIGN_FLAGS = -Svendor/ioscpy/daemon/entitlements.plist
xltouchd_INSTALL_PATH = /Applications/XLStream.app/bin

XLSystemActions_FILES = XLSystemActions.xm
XLSystemActions_CFLAGS = -fobjc-arc -Wall -Wextra -Wno-error -Wno-deprecated-declarations
XLSystemActions_FRAMEWORKS = Foundation UIKit
XLSystemActions_INSTALL_PATH = /Library/MobileSubstrate/DynamicLibraries

# MIT-licensed ioscpy input model.  Its capture code stays dormant because the
# formal Windows client only opens this channel for touch/control messages.
XLTouchActions_FILES = vendor/ioscpy/tweak/Tweak.xm vendor/ioscpy/tweak/Capture.mm vendor/ioscpy/tweak/StreamClient.mm vendor/ioscpy/tweak/Encoder.mm vendor/ioscpy/tweak/InputInjector.mm vendor/ioscpy/tweak/KeyboardSuppression.mm vendor/ioscpy/daemon/Protocol.mm
XLTouchActions_CFLAGS = -fobjc-arc -Ivendor/ioscpy/daemon -Ivendor/ioscpy/tweak -Wall -Wextra -Wno-error -Wno-deprecated-declarations
XLTouchActions_FRAMEWORKS = Foundation UIKit ImageIO MobileCoreServices CoreGraphics IOSurface QuartzCore IOKit VideoToolbox CoreMedia CoreVideo
XLTouchActions_INSTALL_PATH = /Library/MobileSubstrate/DynamicLibraries

include $(THEOS_MAKE_PATH)/tool.mk
include $(THEOS_MAKE_PATH)/tweak.mk
