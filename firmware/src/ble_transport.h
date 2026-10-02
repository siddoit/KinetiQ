/*
 * KINETIQ firmware - BLE NUS-style JSON telemetry transport (watch side).
 *
 * Laptop contract: Nordic-UART-Service layout, one locked-schema JSON object
 * per newline-terminated frame over the TX characteristic (notify), 1 Hz.
 * Matches laptop/receiver/ble_client.py FrameReassembler (newline primary,
 * brace-scanned delimiter-less fallback) and its NUS UUID constants.
 *
 * Thread model: bt_conn callbacks run in the Bluetooth RX / system workqueue
 * context and update the atomic connection flag; ble_transport_send_telemetry
 * is called from comm_thread. The getter stays lock-free via atomic_t.
 * Single-connection assumption: one laptop peer at a time; a new connection
 * replaces the stored reference, a disconnect clears it.
 *
 * Advertising deviation (documented): FINAL_BREAKDOWN mentions "directed
 * advertising", which requires a known peer address and skips first-pair
 * discovery by name. This transport uses BT_LE_ADV_CONN (undirected,
 * connectable) with flags BT_LE_AD_GENERAL | BT_LE_AD_LIMITED plus the
 * complete device name and the 128-bit NUS service UUID in the scan
 * response, so a laptop scanning for name "KINETIQ" or the NUS UUID finds
 * the watch on first pair. Switch to BT_LE_ADV_CONN_DIR once a bonded
 * peer address is stored.
 */

#ifndef BLE_TRANSPORT_H
#define BLE_TRANSPORT_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#define BLE_NUS_SERVICE_UUID "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define BLE_NUS_TX_CHAR_UUID "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
#define BLE_NUS_RX_CHAR_UUID "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define BLE_DEVICE_NAME "KINETIQ"

int ble_transport_init(void);
bool ble_transport_is_connected(void);
int ble_transport_send_telemetry(const char *json_str, size_t len);
void ble_transport_register_rx_callback(void (*cb)(const uint8_t *data, size_t len));
uint32_t ble_transport_tx_count(void);
uint32_t ble_transport_tx_dropped(void);

#endif /* BLE_TRANSPORT_H */
