#include <stdio.h>
#include <stddef.h>
#include <termios.h>
#include <fcntl.h>
#include <gnu/libc-version.h>
int main(void){
  printf("glibc %s\n", gnu_get_libc_version());
  printf("sizeof termios %zu tcflag_t %zu speed_t %zu NCCS %d\n", sizeof(struct termios), sizeof(tcflag_t), sizeof(speed_t), NCCS);
  printf("iflag %zu oflag %zu cflag %zu lflag %zu line %zu cc %zu ispeed %zu ospeed %zu\n",
    offsetof(struct termios,c_iflag),offsetof(struct termios,c_oflag),offsetof(struct termios,c_cflag),offsetof(struct termios,c_lflag),
    offsetof(struct termios,c_line),offsetof(struct termios,c_cc),offsetof(struct termios,c_ispeed),offsetof(struct termios,c_ospeed));
  printf("VMIN %d VTIME %d\n", VMIN, VTIME);
#ifdef B1000000
  printf("B1000000 0%o (0x%x) CBAUD 0%o CBAUDEX 0%o\n", B1000000, B1000000, CBAUD, CBAUDEX);
#endif
  printf("O_RDWR %d O_NOCTTY %d O_NONBLOCK %d CLOCAL 0x%x CREAD 0x%x CS8 0x%x TCSANOW %d TCIFLUSH %d\n", O_RDWR,O_NOCTTY,O_NONBLOCK,CLOCAL,CREAD,CS8,TCSANOW,TCIFLUSH);
  struct termios t = {0};
  int r = cfsetspeed(&t, B1000000);
  printf("cfsetspeed(B1000000) -> %d, cflag & CBAUD = 0%o, c_ispeed %u c_ospeed %u\n", r, t.c_cflag & CBAUD, t.c_ispeed, t.c_ospeed);
  return 0;
}
