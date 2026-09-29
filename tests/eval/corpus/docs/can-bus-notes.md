# CAN Bus Notes

## Bit timing
For 500 kbit/s with a 42 MHz APB1 clock use prescaler 6, BS1 = 11 TQ, BS2 = 2 TQ, SJW = 1 TQ.
The sample point should be around 87.5 % as recommended by CiA.

## Filters
Configure acceptance filters with `HAL_CAN_ConfigFilter()`. In mask mode, a 0 bit in the mask
means "don't care". Use FIFO0 for high-priority IDs.

## Bus-off recovery
Enable `AutoBusOff` so the controller rejoins the bus after 128 occurrences of 11 recessive bits.
