#ifndef EC_TOOLCHAIN_COMPAT_H_
#define EC_TOOLCHAIN_COMPAT_H_

#include <inttypes.h>

/*
 * Debian newlib 4.5 exposes uint64_t but gates PRI*64 on the absent legacy
 * __int64_t_defined macro. This ARM ABI defines uint64_t as unsigned long
 * long. The only upstream use in this build is a disabled random-seed debug
 * log.
 */
#ifndef PRIx64
_Static_assert(__builtin_types_compatible_p(uint64_t, unsigned long long),
	       "Recheck integer format compatibility for this compiler");
#define PRIx64 "llx"
#endif
#endif
