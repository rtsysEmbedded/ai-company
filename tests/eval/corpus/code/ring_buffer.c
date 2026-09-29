#include <stdint.h>
#include <stdbool.h>

/* Lock-free single-producer single-consumer ring buffer used by the UART RX path. */
typedef struct {
    uint8_t *buf;
    uint16_t size;          /* must be a power of two */
    volatile uint16_t head; /* written by ISR */
    volatile uint16_t tail; /* written by main loop */
} ring_buffer_t;

bool rb_push(ring_buffer_t *rb, uint8_t byte)
{
    uint16_t next = (rb->head + 1) & (rb->size - 1);
    if (next == rb->tail) {
        return false; /* full: drop byte, caller counts overflow */
    }
    rb->buf[rb->head] = byte;
    rb->head = next;
    return true;
}

bool rb_pop(ring_buffer_t *rb, uint8_t *out)
{
    if (rb->tail == rb->head) {
        return false; /* empty */
    }
    *out = rb->buf[rb->tail];
    rb->tail = (rb->tail + 1) & (rb->size - 1);
    return true;
}
