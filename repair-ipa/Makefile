TARGET = iphone:clang:latest:15.0
ARCHS = arm64

include $(THEOS)/makefiles/common.mk

APPLICATION_NAME = XLStreamRepair
XLStreamRepair_FILES = main.m RepairViewController.m
XLStreamRepair_FRAMEWORKS = UIKit Foundation
XLStreamRepair_CFLAGS = -fobjc-arc -Wall -Wextra -Wno-error
XLStreamRepair_CODESIGN_FLAGS = -SEntitlements.plist

include $(THEOS_MAKE_PATH)/application.mk
