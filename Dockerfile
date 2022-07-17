FROM python:3.9-alpine as builder

RUN apk update && apk add alpine-sdk tiff-dev jpeg-dev openjpeg-dev zlib-dev
ADD requirements.txt /tmp/
RUN pip3 install --user -r /tmp/requirements.txt && rm /tmp/requirements.txt

FROM python:3.9-alpine as runner
RUN apk update && apk add --no-cache libressl jpeg-dev openjpeg-dev libimagequant-dev tiff-dev freetype-dev libxcb-dev

FROM runner
WORKDIR /CaptchaBot
ENV TZ=Asia/Shanghai

COPY --from=builder /root/.local /usr/local
COPY . /CaptchaBot

CMD ["/usr/local/bin/python", "main.py"]
