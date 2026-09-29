/*
  SPEKTRUM UNIVERSAL SRXL2 -> PC BRIDGE
  =====================================

  WACO/SRXL2 signal -> resistor -> Arduino Nano D8 / ICP1
  Receiver ground   -> Arduino GND

  PC transport:
      1,000,000 baud
      protocol v2
      dynamic 32-bit channel mask

  v2 packet:
      0-1   A5 5A
      2     protocol version = 2
      3     total packet length
      4     sequence
      5     flags (bit0 = failsafe)
      6     RSSI
      7-8   receiver frame loss, little endian
      9-12  active/seen channel mask, little endian
      13..  uint16 channel values for every set mask bit, ascending channel
      last  CRC8 polynomial 0x07 over bytes 2..last-1

  SRXL2 edge decoding is the same hardware-capture architecture that
  proved reliable in testing. Timer0 interrupts remain disabled.
*/

#include <Arduino.h>

const uint8_t SIGNAL_PIN = 8;
const uint16_t BIT_NUMERATOR = 625;
const uint16_t BIT_DENOMINATOR = 36;
const uint16_t FRAME_GAP_TICKS = 400;
const uint16_t PC_SEND_IDLE_TICKS = 1000;

volatile uint16_t edgeBuffer[256];
volatile uint8_t edgeHead = 0;
volatile uint8_t edgeTail = 0;
volatile uint16_t lastCaptureTime = 0;
volatile bool havePreviousCapture = false;

const uint8_t MAX_PACKET_SIZE = 80;
uint8_t packet[MAX_PACKET_SIZE];
uint8_t packetByteCount = 0;
uint8_t expectedPacketLength = 0;
bool inFrame = false;
bool frameComplete = false;
bool framingBad = false;
uint8_t uartPosition = 0;
uint8_t workingByte = 0;

uint16_t channels[32];
uint32_t seenChannels = 0;
int8_t lastRSSI = 0;
uint16_t lastFrameLosses = 0;
bool lastFailsafe = false;

bool pcPacketPending = false;
uint8_t pcSequence = 0;

ISR(TIMER1_CAPT_vect)
{
    uint16_t captured = ICR1;
    bool rising = (TCCR1B & _BV(ICES1)) != 0;

    if (rising)
        TCCR1B &= ~_BV(ICES1);
    else
        TCCR1B |= _BV(ICES1);

    if (!havePreviousCapture)
    {
        lastCaptureTime = captured;
        havePreviousCapture = true;
        return;
    }

    uint16_t delta = captured - lastCaptureTime;
    lastCaptureTime = captured;

    if (delta > 0x7FFF)
        delta = 0x7FFF;

    uint16_t event = delta;
    if (rising)
        event |= 0x8000;

    uint8_t next = (uint8_t)(edgeHead + 1);
    if (next != edgeTail)
    {
        edgeBuffer[edgeHead] = event;
        edgeHead = next;
    }
}

bool getEdgeEvent(uint16_t &event)
{
    if (edgeTail == edgeHead)
        return false;

    event = edgeBuffer[edgeTail];
    edgeTail = (uint8_t)(edgeTail + 1);
    return true;
}

uint16_t srxlCRC(const uint8_t *data, uint8_t length)
{
    uint16_t crc = 0;

    for (uint8_t i = 0; i < length; i++)
    {
        crc ^= ((uint16_t)data[i] << 8);

        for (uint8_t bit = 0; bit < 8; bit++)
        {
            if (crc & 0x8000)
                crc = (crc << 1) ^ 0x1021;
            else
                crc <<= 1;
        }
    }

    return crc;
}

uint8_t pcCRC8(const uint8_t *data, uint8_t length)
{
    uint8_t crc = 0;

    for (uint8_t i = 0; i < length; i++)
    {
        crc ^= data[i];

        for (uint8_t bit = 0; bit < 8; bit++)
        {
            if (crc & 0x80)
                crc = (crc << 1) ^ 0x07;
            else
                crc <<= 1;
        }
    }

    return crc;
}

void startFrame()
{
    inFrame = true;
    frameComplete = false;
    framingBad = false;
    uartPosition = 0;
    workingByte = 0;
    packetByteCount = 0;
    expectedPacketLength = 0;
}

void feedBit(bool level)
{
    if (!inFrame || frameComplete)
        return;

    if (uartPosition == 0)
    {
        workingByte = 0;
        if (level != LOW)
            framingBad = true;
    }
    else if (uartPosition >= 1 && uartPosition <= 8)
    {
        uint8_t bitNumber = uartPosition - 1;
        if (level == HIGH)
            workingByte |= (1 << bitNumber);
    }
    else if (uartPosition == 9)
    {
        if (level != HIGH)
            framingBad = true;

        if (packetByteCount >= MAX_PACKET_SIZE)
        {
            framingBad = true;
            frameComplete = true;
            return;
        }

        packet[packetByteCount++] = workingByte;

        if (packetByteCount == 1 && packet[0] != 0xA6)
        {
            framingBad = true;
            frameComplete = true;
            return;
        }

        if (packetByteCount == 3)
        {
            expectedPacketLength = packet[2];

            if (expectedPacketLength < 5 || expectedPacketLength > MAX_PACKET_SIZE)
            {
                framingBad = true;
                frameComplete = true;
                return;
            }
        }

        if (expectedPacketLength > 0 && packetByteCount >= expectedPacketLength)
            frameComplete = true;
    }

    uartPosition++;
    if (uartPosition >= 10)
        uartPosition = 0;
}

void feedRun(bool level, uint16_t timerCounts)
{
    uint32_t scaled =
        (uint32_t)timerCounts * BIT_DENOMINATOR + 312;

    uint16_t numberOfBits =
        scaled / BIT_NUMERATOR;

    if (numberOfBits == 0)
        numberOfBits = 1;

    if (numberOfBits > 12)
    {
        framingBad = true;
        return;
    }

    for (uint16_t i = 0; i < numberOfBits; i++)
    {
        feedBit(level);

        if (frameComplete)
            break;
    }
}

void processPacket()
{
    if (packetByteCount < 14 || packet[0] != 0xA6)
        return;

    if (
        expectedPacketLength == 0 ||
        packetByteCount != expectedPacketLength
    )
        return;

    uint8_t length = expectedPacketLength;

    uint16_t receivedCRC =
        ((uint16_t)packet[length - 2] << 8)
        |
        packet[length - 1];

    uint16_t calculatedCRC =
        srxlCRC(packet, length - 2);

    if (receivedCRC != calculatedCRC)
        return;

    if (packet[1] != 0xCD)
        return;

    uint8_t command = packet[3];

    if (command != 0x00 && command != 0x01)
        return;

    lastFailsafe = (command == 0x01);
    lastRSSI = (int8_t)packet[5];

    lastFrameLosses =
        (uint16_t)packet[6]
        |
        ((uint16_t)packet[7] << 8);

    uint32_t mask =
        ((uint32_t)packet[8])
        |
        ((uint32_t)packet[9] << 8)
        |
        ((uint32_t)packet[10] << 16)
        |
        ((uint32_t)packet[11] << 24);

    uint8_t offset = 12;

    for (uint8_t channel = 0; channel < 32; channel++)
    {
        if (mask & (1UL << channel))
        {
            if ((uint8_t)(offset + 1) >= (uint8_t)(length - 2))
                return;

            channels[channel] =
                (uint16_t)packet[offset]
                |
                ((uint16_t)packet[offset + 1] << 8);

            offset += 2;
            seenChannels |= (1UL << channel);
        }
    }

    if (seenChannels != 0)
        pcPacketPending = true;
}

void finishFrame()
{
    if (!inFrame)
        return;

    for (uint8_t i = 0; i < 12 && !frameComplete; i++)
        feedBit(HIGH);

    if (
        frameComplete &&
        !framingBad &&
        expectedPacketLength > 0 &&
        packetByteCount == expectedPacketLength
    )
    {
        processPacket();
    }

    inFrame = false;
    frameComplete = false;
}

void processEdge(uint16_t event)
{
    bool newState = (event & 0x8000) != 0;
    uint16_t delta = event & 0x7FFF;

    if (delta > FRAME_GAP_TICKS && newState == LOW)
    {
        finishFrame();
        startFrame();
        return;
    }

    if (!inFrame)
        return;

    feedRun(!newState, delta);
}

void checkIdleFinish()
{
    if (!inFrame)
        return;

    uint16_t now;
    uint16_t last;

    noInterrupts();
    now = TCNT1;
    last = lastCaptureTime;
    interrupts();

    if ((uint16_t)(now - last) > FRAME_GAP_TICKS)
        finishFrame();
}

uint8_t countMaskBits(uint32_t mask)
{
    uint8_t count = 0;
    while (mask)
    {
        count += (mask & 1UL);
        mask >>= 1;
    }
    return count;
}

void sendPCPacket()
{
    if (!pcPacketPending || seenChannels == 0)
        return;

    /*
      Worst case 32 channels:
        13 bytes before values
        64 bytes values
        1 byte CRC
      = 78 bytes
    */
    uint8_t out[78];
    uint8_t offset = 0;

    out[offset++] = 0xA5;
    out[offset++] = 0x5A;
    out[offset++] = 0x02;

    uint8_t lengthIndex = offset++;
    out[offset++] = pcSequence++;
    out[offset++] = lastFailsafe ? 0x01 : 0x00;
    out[offset++] = (uint8_t)lastRSSI;
    out[offset++] = lastFrameLosses & 0xFF;
    out[offset++] = lastFrameLosses >> 8;

    out[offset++] = seenChannels & 0xFF;
    out[offset++] = (seenChannels >> 8) & 0xFF;
    out[offset++] = (seenChannels >> 16) & 0xFF;
    out[offset++] = (seenChannels >> 24) & 0xFF;

    for (uint8_t ch = 0; ch < 32; ch++)
    {
        if (seenChannels & (1UL << ch))
        {
            out[offset++] = channels[ch] & 0xFF;
            out[offset++] = channels[ch] >> 8;
        }
    }

    uint8_t totalLength = offset + 1;
    out[lengthIndex] = totalLength;

    out[offset++] =
        pcCRC8(&out[2], totalLength - 3);

    /*
      Serial.write may briefly block for a packet larger than the AVR's
      TX ring buffer, but at 1 Mbps even the absolute 78-byte worst case
      is only ~0.78 ms on the wire. We only begin sending inside the
      receiver's idle gap, and interrupts remain enabled.
    */
    Serial.write(out, totalLength);

    pcPacketPending = false;
}

void trySendToPC()
{
    if (!pcPacketPending || inFrame)
        return;

    if (edgeHead != edgeTail)
        return;

    uint16_t now;
    uint16_t last;

    noInterrupts();
    now = TCNT1;
    last = lastCaptureTime;
    interrupts();

    if ((uint16_t)(now - last) >= PC_SEND_IDLE_TICKS)
        sendPCPacket();
}

void setup()
{
    Serial.begin(1000000);

    // Disable Arduino Timer0 interrupts: millis/micros/delay are not used.
    TIMSK0 = 0;

    pinMode(SIGNAL_PIN, INPUT);

    TCCR1A = 0;
    TCCR1B = _BV(CS11);
    TCNT1 = 0;

    bool currentState =
        (PINB & _BV(PB0)) != 0;

    if (currentState == LOW)
        TCCR1B |= _BV(ICES1);
    else
        TCCR1B &= ~_BV(ICES1);

    TCCR1B |= _BV(ICNC1);

    TIFR1 = _BV(ICF1);
    TIMSK1 = _BV(ICIE1);
}

void loop()
{
    uint16_t event;

    while (getEdgeEvent(event))
        processEdge(event);

    checkIdleFinish();
    trySendToPC();
}
