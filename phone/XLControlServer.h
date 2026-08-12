#pragma once

#include <stdint.h>

void XLStartControlServer(void);
void XLStartLegacyControlCompatibilityServer(void);
uint32_t XLControlErrorCount(void);
