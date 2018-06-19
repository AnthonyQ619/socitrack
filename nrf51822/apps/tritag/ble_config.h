#ifndef __BLE_CONFIG_H
#define __BLE_CONFIG_H

#include "app_timer.h"

// CONSTS --------------------------------------------------------------------------------------------------------------

// SUMMON URL
#define PHYSWEB_URL "n.ethz.ch/~abiri/d"

// Information
#define APP_COMPANY_IDENTIFIER 0x11BB
#define MANUFACTURER_NAME      "Lab11UMich"
#define MODEL_NUMBER           DEVICE_NAME
#define HARDWARE_REVISION      "A"
#define FIRMWARE_REVISION      "0.1"

// Behaviour
#define UPDATE_RATE             APP_TIMER_TICKS(1000, 0)

#define TRITAG_TIMER_PRESCALER  0
#define TRITAG_MAX_TIMERS       6
#define TRITAG_OP_QUEUE_SIZE    5

// Structs -------------------------------------------------------------------------------------------------------------

typedef struct ble_app_s {
    uint8_t                      current_location[6];    /** Value of num characteristic */
    uint8_t                      app_raw_response_buffer[128]; // Buffer to store raw responses from TriPoint so that it can be sent over BLE
    uint8_t                      app_ranging; // Whether or not the TriPoint module is running and ranging. 1 = yes, 0 = no
    uint8_t                      calibration_index;
} ble_app_t;

#endif
