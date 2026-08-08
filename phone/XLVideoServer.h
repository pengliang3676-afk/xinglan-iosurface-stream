#pragma once

#include <stdint.h>

void XLStartVideoServer(void);
void XLRequestVideoKeyframe(void);
uint32_t XLVideoClientCount(void);
uint32_t XLVideoDroppedFrameCount(void);
