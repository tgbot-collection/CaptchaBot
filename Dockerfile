FROM python:3.9-alpine as builder

ADD requirements.txt /tmp/
RUN pip3 install --user -r /tmp/requirements.txt && rm /tmp/requirements.txt


FROM python:3.9-alpine
WORKDIR /JoinGroup
ENV TZ=Asia/Shanghai

COPY --from=builder /root/.local /usr/local
COPY . /JoinGroup

CMD ["/usr/local/bin/python", "main.py"]