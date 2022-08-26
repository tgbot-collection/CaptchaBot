FROM python:3.10-alpine

RUN apk update && apk add --no-cache alpine-sdk openjpeg-dev zlib-dev  libressl jpeg-dev  \
    libimagequant-dev tiff-dev freetype-dev libxcb-dev
ADD requirements.txt /tmp/
RUN pip3 install -r /tmp/requirements.txt && rm /tmp/requirements.txt && apk del alpine-sdk

WORKDIR /CaptchaBot
ENV TZ=Asia/Shanghai

COPY . /CaptchaBot

CMD ["/usr/local/bin/python", "main.py"]
