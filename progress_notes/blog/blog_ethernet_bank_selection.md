# The MAC Address That Kept Disappearing

## The Symptom

The Seeq 80C03 Ethernet controller was initialized. IRIX brought up the `ec0` interface. ARP requests went out and I could see them in QEMU's SLIRP backend. ARP replies came back. And then... nothing. The replies were delivered to the emulated NIC, written into the RX DMA descriptors, and the IRIX driver simply ignored them. Every single unicast packet was silently dropped.

Broadcast packets worked. ARP requests transmitted fine. But ARP replies, ICMP echo replies, TCP SYNs — anything addressed to our specific MAC address, vanished without a trace.

## The Seeq 80C03

The Seeq 80C03 EDLC (Ethernet Data Link Controller) is a simple chip by modern standards. It has a set of registers (0-7) for station address, transmit/receive control, and status. The HPC3's DMA engine handles the actual packet data. The Seeq just does framing, address filtering, and media access.

IRIX programs the MAC address by writing six bytes to registers 0-5. Or rather, it writes to what it *thinks* are registers 0-5. The Seeq has a trick: the TX command register (register 7) contains two bank select bits.

### Bank Selection

Bits [6:5] of the TX command register control which physical register set the address space maps to:

```
TX cmd bits [6:5]:
  0x00 → Station address (regs 0-5 = MAC bytes 0-5)
  0x20 → Multicast hash filter low (regs 0-5 = hash bytes 0-5)
  0x40 → Multicast hash filter high (regs 0-1) + control (reg 3) + config (reg 4)
  0x60 → Reserved
```

IRIX's ethernet driver initializes the Seeq in this order:

1. Set bank 0x00 (station address bank)
2. Write the MAC address to registers 0-5
3. Set bank 0x20 (multicast hash filter low)
4. Write 6 bytes of multicast hash filter
5. Set bank 0x40 (multicast hash filter high + control/config)
6. Write control and configuration registers

Our implementation had no bank selection logic. Every write to registers 0-5 went to the station address, regardless of the TX command register's bank bits. The MAC address was written correctly in step 2. Then step 4 overwrote it with the multicast hash filter values, six bytes of zeros (IRIX starts with no multicast groups enabled).

The station address changed from `08:00:69:xx:xx:xx` to whatever residual bytes remained from the bank 0x40 writes, a mix of control register values and zeros that bore no resemblance to any valid MAC address. The Seeq's address filter compared incoming packets against this noise address. Only broadcast packets (destination `FF:FF:FF:FF:FF:FF`) matched. Every unicast packet was rejected. 

## The Fix

Check the bank select bits before deciding where register writes go:

```c
if (reg <= 5) {
    uint8_t bank = s->seeq_tx_cmd & SEEQ_TXC_BANK_MASK;
    switch (bank) {
    case 0x00:  /* Station address */
        s->seeq_station_addr[reg] = val;
        break;
    case 0x20:  /* Multicast hash low */
        s->seeq_mcast_low[reg] = val;
        break;
    case 0x40:  /* Multicast hash high / control / config */
        if (reg <= 1) s->seeq_mcast_high[reg] = val;
        else if (reg == 3) s->seeq_control = val;
        else if (reg == 4) s->seeq_config = val;
        break;
    }
}
```

Now the multicast hash writes go to the multicast hash registers, and the station address survives initialization.

## The Second Bug: Upside-Down Ownership

With the MAC address fixed, ARP replies were accepted by the address filter. The Seeq's RX logic captured the packet, the HPC3 DMA engine wrote it into the guest's RX descriptor buffer, and the DMA engine updated the descriptor's status word. The IRIX driver's interrupt handler fired.

And it still couldn't see the packet.

The RX descriptor has a `r_rown` bit (bit 14 of the BC/status word). The semantics are:

- `r_rown = 1` → Hardware owns this descriptor (waiting to be filled)
- `r_rown = 0` → Software owns this descriptor (data ready to process)

The IRIX interrupt handler loops through the descriptor chain looking for descriptors it owns:

```c
while (!rd_chain->r_rown) {
    /* process received packet */
    rd_chain = rd_chain->r_nrdesc;
}
```

We were *setting* `r_rown` after DMA completion, marking the descriptor as "hardware owns." The driver saw `r_rown = 1` for every descriptor and concluded there were no packets to process. The interrupt fired, the handler ran, found nothing to do, and returned.

The fix: clear `HPC3_ENET_BC_ROWN` in the writeback after DMA. MAME confirms the correct behavior, it writes only the 16-bit byte count to `desc+6`, which inherently leaves bit 14 clear.

## The Third Bug: The Reset That Wasn't

Every time IRIX acknowledged an ethernet interrupt, it wrote `0x03` to the ENET_MISC register (RESET + INT bits), then `0x00`. My implementation treated every write with the RESET bit set as a full device reset, thus clearing the station address, RX DMA state, and all Seeq registers.

IRIX writes RESET on every interrupt acknowledge. If every interrupt clears the station address, the MAC disappears after the first received packet, and we're back to the original problem.

The real solution was recognizing that IRIX's "reset" here isn't a full hardware reset. It's a reset of the interrupt status. The ENET_MISC RESET bit resets the Seeq's interrupt logic, not its entire state. The station address, configuration, and DMA state all survive.

We changed the RESET handler to only reset the interrupt-related state: clear pending interrupt flags, reset the RX/TX status bits. The station address and configuration persist.

## The Fourth Complication: Timing

With all three bugs fixed, packets flowed in both directions. But the timing was terrible:

```
64 bytes from 10.0.2.2: icmp_seq=1 ttl=255 time=2083.171 ms
64 bytes from 10.0.2.2: icmp_seq=3 ttl=255 time=143.947 ms
```

Two-second round trip times. Missed pings (seq=2 is absent). This wasn't a bug in our ethernet implementation — it was a consequence of `-icount shift=0,sleep=off` making virtual time race ahead of real time. The `select()` call in the ping utility was using lbolt-derived timeouts, and lbolt was racing, so the timeout expired before SLIRP had time to deliver the reply.

The full solution to this would come later, with the real-time counter patch to the IRIX kernel. But even with the timing issues, networking was *functional*. ARP resolution worked. TCP connections established. Telnet from the host to IRIX's login prompt worked (with SLIRP port forwarding). The kernel could do everything a networked IRIX system needs to do — just with inflated latency numbers from the VM's perspective.

## A Late Discovery: Deferred RX

There was one more piece needed to make networking reliable: deferred RX processing. When SLIRP delivers a packet to our `receive()` callback, the IRIX kernel might not have RX DMA descriptors ready yet, the driver initializes them lazily, and at the moment the first packet arrives (usually an ARP reply to our own ARP request), the descriptor chain may be empty.

My initial approach was to return 0 from `receive()` (telling QEMU "I can't accept this packet now") and hope QEMU would retry. But QEMU's network backend doesn't retry indefinitely, it may drop the packet or delay significantly.

The fix was deferred RX: when a packet arrives and no RX descriptors are available, we save the packet in a buffer. When the IRIX driver programs the RX DMA registers (setting up the descriptor chain), we check for deferred packets and deliver them immediately. This ensures the first ARP reply is never lost, even if it arrives before the driver is fully initialized.

```c
/* In the ENET RX DMA register write handler: */
if (s->enet_rx_deferred_len > 0) {
    /* Driver just set up RX descriptors — deliver the saved packet */
    sgi_hpc3_enet_receive_packet(s, s->enet_rx_deferred_buf,
                                  s->enet_rx_deferred_len);
    s->enet_rx_deferred_len = 0;
}
```

## The Result

After all four fixes:

```
IRIS# ping -c 5 10.0.2.2
PING 10.0.2.2 (10.0.2.2): 56 data bytes
64 bytes from 10.0.2.2: icmp_seq=0 ttl=255 time=2083.171 ms
64 bytes from 10.0.2.2: icmp_seq=1 ttl=255 time=143.947 ms
64 bytes from 10.0.2.2: icmp_seq=2 ttl=255 time=1152.003 ms
64 bytes from 10.0.2.2: icmp_seq=3 ttl=255 time=1006.449 ms
64 bytes from 10.0.2.2: icmp_seq=4 ttl=255 time=851.562 ms
```

Five packets, five replies. The latency numbers are inflated by the virtual time racing issue (later fixed by the kernel real-time counter patch, which brought RTTs down to sub-2ms), but the packets are flowing. Telnet works. The system is networked.

## Reflections

The bank selection bug is a classic emulation trap: the hardware has a feature that's fully documented, but the documentation is in a chip datasheet that you might not read until packets are dropping. The Seeq 80C03 data sheet describes bank selection clearly. IRIX's `seeq.h` header defines `SEEQ_TXC_BANK_MASK`. The driver's init code explicitly switches banks. All the clues were there, I just hadn't connected them.

The `r_rown` polarity bug is subtler. Bit names that suggest ownership direction are notoriously confusing across hardware designs. "Rown" could mean "receiver owns" or "ready-own" or something else entirely. The only reliable way to determine polarity is to look at the driver code (`while (!rd_chain->r_rown)`) and work backwards: if the driver loops while `!rown`, then `rown=0` means "data ready."

The reset semantics bug is the most forgivable. Without reading the Seeq's documentation on what exactly the reset line affects, it's reasonable to assume "reset means reset everything." The distinction between "reset interrupt logic" and "reset everything" requires hardware-specific knowledge.

Four bugs, each preventing the next from being visible. Fix the MAC address, discover the ownership polarity. Fix the polarity, discover the reset semantics. Fix the reset, discover the timing. Fix the timing (later), and networking just works. Emulation debugging is archaeology. Each layer removed reveals the next.
