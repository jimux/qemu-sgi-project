/*
 * clock_patch.c - QEMU real-time gate for nano_delay()
 *
 * Overrides nano_delay() from os/clock.c to gate on QEMU MC_REALTIME_CTR
 * for correct real-time delays under -icount shift=0,sleep=off.
 *
 * Under icount sleep=off, virtual time races during MIPS WAIT idle.
 * The callout queue fires callbacks after N virtual ticks, which pass
 * in microseconds of real time.  This wrapper lets the virtual callout
 * fire (waking the thread), checks real elapsed time, and re-enters
 * sleep if real time hasn't elapsed yet.
 */

#include <sys/types.h>
#include <sys/param.h>
#include <sys/pda.h>
#include <sys/proc.h>
#include <sys/sema.h>
#include <sys/ktime.h>
#include <sys/time.h>
#include <sys/systm.h>

extern volatile uint32_t *qemu_rt_ctr;

/*
 * nano_delay - delay current thread, non-breakable.
 *
 * Every delay(ticks) call flows through here, including sockd() which
 * drives all TCP/UDP timers.  Fixing this one function makes
 * periodic_timeouts() fire at correct real intervals.
 */
void
nano_delay(timespec_t *ts)
{
	uint32_t rt_start, rt_now, rt_elapsed_us, rt_target_us;
	timespec_t rem;

	if (ts->tv_sec == 0 && ts->tv_nsec == 0)
		return;

	/* Cap at ~35 min to avoid uint32 overflow */
	if (ts->tv_sec > 2000) {
		kthread_t *kt = curthreadp;
		int s = kt_lock(kt);
		kt_timedwait(kt, 0, s, 1, ts, NULL);
		return;
	}

	rt_target_us = (uint32_t)(ts->tv_sec * 1000000)
	             + (uint32_t)(ts->tv_nsec / 1000);
	if (rt_target_us == 0)
		rt_target_us = 1;
	rt_start = *qemu_rt_ctr;

	/* Under icount, 1 virtual tick ~= 8-10x real time, so sleep in
	 * 1-tick increments and loop until real time is satisfied. */
	rem.tv_sec = 0;
	rem.tv_nsec = 10000000;  /* 1 tick = 10ms virtual */
	for (;;) {
		kthread_t *kt = curthreadp;
		int s = kt_lock(kt);
		kt_timedwait(kt, 0, s, 1, &rem, NULL);

		rt_now = *qemu_rt_ctr;
		rt_elapsed_us = rt_now - rt_start;
		if (rt_elapsed_us >= rt_target_us)
			break;
	}
}
