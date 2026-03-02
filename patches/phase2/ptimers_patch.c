/*
 * ptimers_patch.c - QEMU real-time gate for nanosleep/sginap
 *
 * Overrides nanosleep(), nanosleep_common(), sginap() from os/ptimers.c.
 * nanosleep()/sleep() do NOT go through nano_delay() - they call
 * ut_timedsleepsig() -> sv_bitlock_timedwait_sig() -> sv_set_timeout()
 * -> itimeout_nothrd().  Separate code path, needs its own real-time gate.
 */

#include <sys/types.h>
#include <sys/debug.h>
#include <sys/errno.h>
#include <sys/callo.h>
#include <sys/par.h>
#include <sys/param.h>
#include <sys/pda.h>
#include <ksys/vproc.h>
#include <sys/kabi.h>
#include <sys/systm.h>
#include <sys/prctl.h>
#include <sys/ksignal.h>
#include <sys/ktime.h>
#include <sys/signal.h>
#include <sys/kopt.h>
#include <sys/ptimers.h>
#include <sys/xlate.h>
#include <sys/schedctl.h>
#include <sys/clksupport.h>

extern volatile uint32_t *qemu_rt_ctr;
extern int fastick, fasthz;

/* Gang scheduling states (from sys/space.h - not shipped in /usr/include) */
#ifndef GANG_NONE
#define GANG_NONE  0
#define GANG_UNDEF 7
#endif

int irix5_to_timespec(enum xlate_mode, void *, int, xlate_info_t *);
int timespec_to_irix5(void *, int, xlate_info_t *);

struct nanosleepa {
	struct timespec *rqtp;
	struct timespec *rmtp;
};

struct sginapa {
	long	ticks;
};

/* Forward declaration */
static int nanosleep_common(struct timespec *, struct timespec *, int);

int
nanosleep(struct nanosleepa *uap)
{
	struct timespec time, remtime;
	int error;
#if _MIPS_SIM == _ABI64
	int abi = get_current_abi();
#endif

	if (COPYIN_XLATE(uap->rqtp, &time, sizeof time,
					irix5_to_timespec, abi, 1))
		return EFAULT;
	if (time.tv_nsec < 0 || time.tv_nsec >= NSEC_PER_SEC)
		return EINVAL;

	error = nanosleep_common(&time, &remtime,
				 kt_has_fastpriv(curthreadp) ? SVTIMER_FAST : 0);
	if (error == EINTR) {
		if (uap->rmtp) {
			if (XLATE_COPYOUT(&remtime, uap->rmtp, sizeof remtime,
					timespec_to_irix5, abi, 1))
				return EFAULT;
		}
	}

	return error;
}

static
int
nanosleep_common(struct timespec *ts, struct timespec *rts, int svtimer_flags)
{
	uthread_t *ut = curuthread;
	kthread_t *kt = curthreadp;
	int s, error;
	uint32_t rt_start, rt_now, rt_elapsed_us, rt_target_us;
	timespec_t rem;

	/* Compute real-time target (cap at ~35 min) */
	if (ts->tv_sec > 2000) {
		s = ut_lock(ut);
		error = ut_timedsleepsig(ut, &kt->k_timewait, 0, s,
					  svtimer_flags, ts, rts);
		return (error == -1) ? EINTR : 0;
	}

	rt_target_us = (uint32_t)(ts->tv_sec * 1000000)
	             + (uint32_t)(ts->tv_nsec / 1000);
	rt_start = *qemu_rt_ctr;
	/* Sleep 1 tick at a time so the real-time check fires every ~85ms
	 * real under icount, giving correct total real time. */
	rem.tv_sec = 0;
	rem.tv_nsec = 10000000;  /* 1 tick = 10ms virtual */

	for (;;) {
		s = ut_lock(ut);
		error = ut_timedsleepsig(ut, &kt->k_timewait, 0, s,
					  svtimer_flags, &rem, rts);
		if (error == -1)
			return EINTR;  /* Signal interrupted - return immediately */

		rt_now = *qemu_rt_ctr;
		rt_elapsed_us = rt_now - rt_start;
		if (rt_elapsed_us >= rt_target_us)
			break;
	}

	/* Correct remaining time for caller */
	if (rts) {
		rts->tv_sec = 0;
		rts->tv_nsec = 0;
	}
	return 0;
}

int
sginap(struct sginapa *uap, rval_t *rvp)
{
	timespec_t ts, rts;

	if (uap->ticks == 0) {
		/*
		 * Zero ticks means a voluntary reschedule
		 */
		if (curuthread->ut_gstate == GANG_UNDEF ||
			curuthread->ut_gstate == GANG_NONE)
			user_resched(RESCHED_Y);
		return 0;
	}
	tick_to_timespec(uap->ticks, &ts, NSEC_PER_TICK);
	if (nanosleep_common(&ts, &rts, SVTIMER_TRUNC) == EINTR)
		rvp->r_val1 = timespec_to_tick(&rts, NSEC_PER_TICK);
	return 0;
}
