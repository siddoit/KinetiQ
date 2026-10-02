/*
 * KINETIQ firmware - BLE NUS-style transport implementation.
 *
 * Zephyr 4.2-era API usage: bt_enable(NULL) synchronous bring-up,
 * BT_CONN_CB_DEFINE static callback block, BT_GATT_SERVICE_DEFINE with
 * BT_UUID_128 characteristics, bt_le_adv_start with BT_LE_ADV_CONN
 * (undirected connectable; see header for the directed-advertising note),
 * per-chunk bt_gatt_notify on the stored TX value attribute, effective MTU
 * via bt_gatt_get_mtu with a 23-3=20 fallback. CCCD tracked in the TX path:
 * a notify with indications disabled errors out and is counted, never spun on.
 *
 * Byte order: BT_UUID_128_ENCODE takes the UUID in display order and stores
 * little-endian on the wire. NUS service 6E400001-B5A3-F393-E0A9-E50E24DCCA9E
 * encodes to LE bytes 9E CA DC 24 0E E5 A9 E0 93 F3 A3 B5 01 00 40 6E; the AD
 * UUID128_ALL array below uses that same order, matching ble_client.py.
 */

#if defined(__ZEPHYR__)

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/util.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>

#include <string.h>

#include "ble_transport.h"

LOG_MODULE_REGISTER(ble_transport, LOG_LEVEL_INF);

#define BLE_TX_CHUNK_FALLBACK 20U

static atomic_t s_connected = ATOMIC_INIT(0);
static struct bt_conn *s_conn;
static atomic_t s_tx_count = ATOMIC_INIT(0);
static atomic_t s_tx_dropped = ATOMIC_INIT(0);
static atomic_t s_notify_enabled = ATOMIC_INIT(0);
static bool s_init_done;
static void (*s_rx_cb)(const uint8_t *data, size_t len);

static void on_connected(struct bt_conn *conn, uint8_t err)
{
	char addr[BT_ADDR_LE_STR_LEN];

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	if (err != 0U) {
		LOG_WRN("BLE connect failed %u from %s", err, addr);
		return;
	}

	if (s_conn != NULL) {
		bt_conn_unref(s_conn);
	}
	s_conn = bt_conn_ref(conn);
	atomic_set(&s_connected, 1);
	LOG_INF("BLE connected: %s", addr);
}

static void on_disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];

	ARG_UNUSED(conn);

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	if (s_conn != NULL) {
		bt_conn_unref(s_conn);
		s_conn = NULL;
	}
	atomic_set(&s_connected, 0);
	atomic_set(&s_notify_enabled, 0);
	LOG_INF("BLE disconnected: %s (reason %u)", addr, reason);
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
	.connected = on_connected,
	.disconnected = on_disconnected,
};

static ssize_t nus_rx_write(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			    const void *buf, uint16_t len, uint16_t offset, uint8_t flags)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(offset);
	ARG_UNUSED(flags);

	if (s_rx_cb != NULL && len > 0U) {
		s_rx_cb((const uint8_t *)buf, (size_t)len);
	}

	return (ssize_t)len;
}

static ssize_t nus_rx_read(struct bt_conn *conn, const struct bt_gatt_attr *attr,
			   void *buf, uint16_t len, uint16_t offset)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(buf);
	ARG_UNUSED(len);
	ARG_UNUSED(offset);

	return -ENOTSUP;
}

static void nus_tx_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	ARG_UNUSED(attr);

	atomic_set(&s_notify_enabled, (value & BT_GATT_CCC_NOTIFY) != 0U ? 1 : 0);
	LOG_DBG("BLE TX notify %s", atomic_get(&s_notify_enabled) ? "on" : "off");
}

BT_GATT_SERVICE_DEFINE(nus_service,
	BT_GATT_PRIMARY_SERVICE(BT_UUID_128_ENCODE(0x6E400001, 0xB5A3, 0xF393,
						   0xE0A9, 0xE50E24DCCA9E)),
	BT_GATT_CHARACTERISTIC(BT_UUID_128_ENCODE(0x6E400003, 0xB5A3, 0xF393,
						  0xE0A9, 0xE50E24DCCA9E),
			       BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_READ,
			       NULL, NULL, NULL),
	BT_GATT_CCC(nus_tx_ccc_changed, BT_GATT_PERM_WRITE),
	BT_GATT_CHARACTERISTIC(BT_UUID_128_ENCODE(0x6E400002, 0xB5A3, 0xF393,
						  0xE0A9, 0xE50E24DCCA9E),
			       BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP,
			       BT_GATT_PERM_WRITE,
			       nus_rx_read, nus_rx_write, NULL),
);

#define NUS_TX_VALUE_ATTR_INDEX 2U

static const struct bt_data ad_data[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_LIMITED)),
	BT_DATA_BYTES(BT_DATA_UUID128_ALL,
		      0x9E, 0xCA, 0xDC, 0x24, 0x0E, 0xE5, 0xA9, 0xE0,
		      0x93, 0xF3, 0xA3, 0xB5, 0x01, 0x00, 0x40, 0x6E),
};

static const struct bt_data sd_data[] = {
	BT_DATA(BT_DATA_NAME_COMPLETE, BLE_DEVICE_NAME, sizeof(BLE_DEVICE_NAME) - 1U),
	BT_DATA_BYTES(BT_DATA_UUID128_ALL,
		      0x9E, 0xCA, 0xDC, 0x24, 0x0E, 0xE5, 0xA9, 0xE0,
		      0x93, 0xF3, 0xA3, 0xB5, 0x01, 0x00, 0x40, 0x6E),
};

int ble_transport_init(void)
{
	int rc;

	if (s_init_done) {
		return 0;
	}

	atomic_set(&s_connected, 0);
	atomic_set(&s_tx_count, 0);
	atomic_set(&s_tx_dropped, 0);
	atomic_set(&s_notify_enabled, 0);

	rc = bt_enable(NULL);
	if (rc != 0) {
		LOG_WRN("BLE bt_enable failed: %d", rc);
		return rc;
	}

	rc = bt_le_adv_start(BT_LE_ADV_CONN, ad_data, ARRAY_SIZE(ad_data),
			     sd_data, ARRAY_SIZE(sd_data));
	if (rc != 0) {
		LOG_WRN("BLE advertising start failed: %d", rc);
		return rc;
	}

	s_init_done = true;
	LOG_INF("BLE up: name %s, NUS advertising", BLE_DEVICE_NAME);

	return 0;
}

bool ble_transport_is_connected(void)
{
	return atomic_get(&s_connected) != 0;
}

static int notify_chunk(const uint8_t *data, size_t chunk_len)
{
	const struct bt_gatt_attr *attr;
	uint16_t mtu;
	int rc;

	if (s_conn == NULL) {
		return -ENOTCONN;
	}

	mtu = bt_gatt_get_mtu(s_conn);
	if (mtu < 3U) {
		mtu = 23U;
	}
	ARG_UNUSED(mtu);

	attr = &nus_service.attrs[NUS_TX_VALUE_ATTR_INDEX];
	rc = bt_gatt_notify(s_conn, attr, data, (uint16_t)chunk_len);
	if (rc != 0) {
		atomic_inc(&s_tx_dropped);
		if (rc == -ENOMEM || rc == -EAGAIN) {
			k_yield();
			rc = bt_gatt_notify(s_conn, attr, data, (uint16_t)chunk_len);
			if (rc != 0) {
				atomic_inc(&s_tx_dropped);
				LOG_WRN("BLE tx dropped (%d)", rc);
				return rc;
			}
		} else {
			LOG_WRN("BLE tx failed (%d)", rc);
			return rc;
		}
	}

	atomic_inc(&s_tx_count);
	return 0;
}

int ble_transport_send_telemetry(const char *json_str, size_t len)
{
	uint16_t mtu;
	size_t max_chunk;
	size_t off = 0;
	size_t sent = 0;
	uint8_t nl = (uint8_t)'\n';
	int rc;

	if (json_str == NULL) {
		return -EINVAL;
	}
	if (atomic_get(&s_connected) == 0 || s_conn == NULL) {
		return -ENOTCONN;
	}

	mtu = bt_gatt_get_mtu(s_conn);
	if (mtu < 3U + 1U) {
		max_chunk = BLE_TX_CHUNK_FALLBACK;
	} else {
		max_chunk = (size_t)mtu - 3U;
	}

	while (off < len) {
		size_t chunk = len - off;

		if (chunk > max_chunk) {
			chunk = max_chunk;
		}
		rc = notify_chunk((const uint8_t *)json_str + off, chunk);
		if (rc != 0) {
			return rc;
		}
		off += chunk;
		sent += chunk;
	}

	rc = notify_chunk(&nl, 1U);
	if (rc != 0) {
		return rc;
	}
	sent += 1U;

	LOG_DBG("BLE tx frame %u byte(s)", (unsigned int)sent);

	return (int)sent;
}

void ble_transport_register_rx_callback(void (*cb)(const uint8_t *data, size_t len))
{
	s_rx_cb = cb;
}

uint32_t ble_transport_tx_count(void)
{
	return (uint32_t)atomic_get(&s_tx_count);
}

uint32_t ble_transport_tx_dropped(void)
{
	return (uint32_t)atomic_get(&s_tx_dropped);
}

#else /* __ZEPHYR__ */

#include <stdio.h>
#include <string.h>

#include "ble_transport.h"

static int stub_connected;
static uint32_t stub_tx_count;
static uint32_t stub_tx_dropped;
static void (*stub_rx_cb)(const uint8_t *data, size_t len);

int ble_transport_init(void)
{
	stub_tx_count = 0U;
	stub_tx_dropped = 0U;
	fprintf(stderr, "ble_transport stub: no Zephyr, init ok\n");
	return 0;
}

bool ble_transport_is_connected(void)
{
	return stub_connected != 0;
}

int ble_transport_send_telemetry(const char *json_str, size_t len)
{
	if (json_str == NULL) {
		return -22;
	}
	if (!stub_connected) {
		return -107;
	}
	stub_tx_count++;
	fprintf(stderr, "ble_transport stub tx %u byte(s)\n", (unsigned int)(len + 1U));
	return (int)(len + 1U);
}

void ble_transport_register_rx_callback(void (*cb)(const uint8_t *data, size_t len))
{
	stub_rx_cb = cb;
}

uint32_t ble_transport_tx_count(void)
{
	return stub_tx_count;
}

uint32_t ble_transport_tx_dropped(void)
{
	return stub_tx_dropped;
}

#endif /* __ZEPHYR__ */
