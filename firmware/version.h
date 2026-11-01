#ifndef EC_VERSION_H
#define EC_VERSION_H
#ifndef EC_VERSION_MAJOR
#define EC_VERSION_MAJOR 0
#endif
#ifndef EC_VERSION_MINOR
#define EC_VERSION_MINOR 12
#endif
#define EC_STRINGIFY_(x) #x
#define EC_STRINGIFY(x) EC_STRINGIFY_(x)
#define EC_VERSION                                                             \
	EC_STRINGIFY(EC_VERSION_MAJOR) "." EC_STRINGIFY(EC_VERSION_MINOR)
#define EC_BOOT_VERSION "0.2"
#endif
