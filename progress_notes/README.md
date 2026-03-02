# Progress Notes

Implementation notes for the SGI QEMU emulation project. For authoritative
project status, milestones, and test file map, see [`CLAUDE.md`](../CLAUDE.md).

## Structure

Notes are organized by platform, with cross-platform material at the root.

### Cross-Platform

| File | Contents |
|------|----------|
| [`lessons_learned.md`](lessons_learned.md) | Hardware gotchas, QEMU framework pitfalls, MAME translation, debugging techniques |
| [`multi_platform_status.md`](multi_platform_status.md) | IP20/IP22/IP24/IP26/IP28 machine types, PROM test matrix, hardware differences |
| [`sgi_graphics_architectures.md`](sgi_graphics_architectures.md) | Comparative analysis of Newport, IMPACT, InfiniteReality, CRM, and other SGI graphics |
| [`community_projects_and_resources.md`](community_projects_and_resources.md) | External projects, tools, documentation, and communities |
| [`irix_source_trees.md`](irix_source_trees.md) | Map of available IRIX source trees (6.5.5f, 6.5.7m) |
| [`irix_655_build_system.md`](irix_655_build_system.md) | IRIX 6.5.5 build system analysis |
| [`irix_custom_smp_platform.md`](irix_custom_smp_platform.md) | IRIX kernel SMP and custom platform analysis |

### [`indy/`](indy/) — SGI Indy (IP24)

| File | Contents |
|------|----------|
| [`indy_boot_milestones.md`](indy/indy_boot_milestones.md) | Phase-by-phase timeline from first register access to IRIX desktop |
| [`indy_hardware_devices.md`](indy/indy_hardware_devices.md) | Newport, WD33C93, HPC3 DMA, INT3, serial — device-level notes |
| [`benchmark_results.md`](indy/benchmark_results.md) | Boot timing, PROM/kernel performance, icount analysis |
| [`virtual_time_and_timing.md`](indy/virtual_time_and_timing.md) | `-icount shift=0,sleep=off` analysis, bare-metal benchmarks, timing chain; **networking with different icount settings** |
| [`irix_installation_guide.md`](indy/irix_installation_guide.md) | Full IRIX 6.5 installation procedure on emulated Indy |
| [`direct_kernel_boot.md`](indy/direct_kernel_boot.md) | `-kernel` boot path: ARCS hypercall device, trampoline, callbacks |
| [`newport_xsgi_milestone.md`](indy/newport_xsgi_milestone.md) | Xsgi X server bring-up, VRINT pulse model, REX3 drawing |
| [`newport_display_pipeline_debug.md`](indy/newport_display_pipeline_debug.md) | DID/XMAP/CMAP/RAMDAC pipeline debugging |
| [`newport_rgb_mode_fix.md`](indy/newport_rgb_mode_fix.md) | Newport RGB mode and depth conversion fix |
| [`xdm_graphical_login_fix.md`](indy/xdm_graphical_login_fix.md) | xdm `grabServer: False` fix for graphical login |
| [`keyboard_mouse_input.md`](indy/keyboard_mouse_input.md) | PS/2 keyboard/mouse via 8042 in IOC2 |
| [`seeq_ethernet_implementation.md`](indy/seeq_ethernet_implementation.md) | Seeq 80C03 ethernet, bank selection, SLIRP networking |
| [`hal2_audio_architecture.md`](indy/hal2_audio_architecture.md) | HAL2 audio controller architecture and stub implementation |
| [`int3_interrupt_storm_fix.md`](indy/int3_interrupt_storm_fix.md) | INT3 spurious interrupt filtering for unimplemented hardware |
| [`multipass_dma_fix.md`](indy/multipass_dma_fix.md) | HPC3 SCSI DMA multi-pass transfer fix |
| [`serial_silence_investigation.md`](indy/serial_silence_investigation.md) | Z85C30 SCC TX investigation, polled vs interrupt-driven output |
| [`logs/`](indy/logs/) | Selected log files from Indy boot debugging |

### [`o2/`](o2/) — SGI O2 (IP32)

| File | Contents |
|------|----------|
| [`o2_implementation.md`](o2/o2_implementation.md) | O2 PROM boot, disk boot chain, kernel crash analysis |
| [`o2_hardware_reference.md`](o2/o2_hardware_reference.md) | CRIME/MACE/CRM hardware register reference |
| [`ip32_prom_reverse_engineering.md`](o2/ip32_prom_reverse_engineering.md) | IP32 PROM reverse engineering notes |
| [`ip32prom_decompiler_tool.md`](o2/ip32prom_decompiler_tool.md) | IP32 PROM decompiler tool documentation |
