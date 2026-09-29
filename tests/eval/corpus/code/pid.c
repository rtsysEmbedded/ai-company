#include "pid.h"

/* PID controller with anti-windup clamping, called every 1 ms from the control task. */
float pid_update(pid_t *p, float setpoint, float measured)
{
    float err = setpoint - measured;
    p->integral += err * p->dt;
    if (p->integral > p->i_max) p->integral = p->i_max;
    if (p->integral < -p->i_max) p->integral = -p->i_max;
    float deriv = (err - p->prev_err) / p->dt;
    p->prev_err = err;
    return p->kp * err + p->ki * p->integral + p->kd * deriv;
}

void pid_reset(pid_t *p)
{
    p->integral = 0.0f;
    p->prev_err = 0.0f;
}
