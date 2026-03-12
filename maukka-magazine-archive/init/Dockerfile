FROM alpine:3.22

WORKDIR /seed

COPY seed/ /seed/
COPY init/init.sh /init.sh

RUN chmod +x /init.sh

CMD ["/init.sh"]
