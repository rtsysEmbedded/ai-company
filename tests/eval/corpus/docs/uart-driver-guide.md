# UART Driver Guide

## Initialization
Call `HAL_UART_Init()` after filling `UART_HandleTypeDef`. Set `Init.BaudRate` (e.g. 115200),
`Init.WordLength`, `Init.StopBits` and `Init.Parity`. The peripheral clock must be enabled first
with `__HAL_RCC_USART2_CLK_ENABLE()`.

## DMA reception
Use `HAL_UARTEx_ReceiveToIdle_DMA()` for variable-length frames. The idle-line event fires
`HAL_UARTEx_RxEventCallback()` with the number of received bytes. Disable the half-transfer
interrupt with `__HAL_DMA_DISABLE_IT(&hdma_usart2_rx, DMA_IT_HT)` to avoid double callbacks.

## Error handling
An overrun error (ORE) happens when a new byte arrives before the previous one was read.
Clear it with `__HAL_UART_CLEAR_OREFLAG()` in `HAL_UART_ErrorCallback()` and restart reception.
