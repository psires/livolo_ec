#ifndef EC_NRF_ERROR_COMPAT_H
#define EC_NRF_ERROR_COMPAT_H
/* Minimal nRF5 SDK ABI constants used by the standalone driver's FEM stubs.
 * No SoftDevice or SDK error implementation is linked.
 */
#define NRF_SUCCESS 0u
#define NRF_ERROR_NOT_SUPPORTED 6u
#define NRF_ERROR_INVALID_STATE 8u
#define NRF_ERROR_FORBIDDEN 15u
#endif
