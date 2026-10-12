#include <stdio.h>
#include <stddef.h>
#include <termios.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <IOKit/serial/ioss.h>
int main(void){
  printf("sizeof termios %zu\n", sizeof(struct termios));
  printf("iflag %zu oflag %zu cflag %zu lflag %zu cc %zu ispeed %zu ospeed %zu\n",
    offsetof(struct termios,c_iflag),offsetof(struct termios,c_oflag),offsetof(struct termios,c_cflag),
    offsetof(struct termios,c_lflag),offsetof(struct termios,c_cc),offsetof(struct termios,c_ispeed),offsetof(struct termios,c_ospeed));
  printf("sizeof tcflag_t %zu speed_t %zu NCCS %d VMIN %d VTIME %d\n", sizeof(tcflag_t), sizeof(speed_t), NCCS, VMIN, VTIME);
  printf("O_RDWR %d O_NOCTTY %d O_NONBLOCK %d CLOCAL 0x%x CREAD 0x%x CS8 0x%x TCSANOW %d TCIOFLUSH %d\n", O_RDWR,O_NOCTTY,O_NONBLOCK,CLOCAL,CREAD,CS8,TCSANOW,TCIOFLUSH);
  printf("IOSSIOSPEED 0x%lx\n", (unsigned long)IOSSIOSPEED);
  return 0;
}
