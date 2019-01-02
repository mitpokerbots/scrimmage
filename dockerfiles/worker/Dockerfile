FROM python:2.7-slim

RUN useradd -ms /bin/bash worker
USER worker

# copy only the files needed for pip install
COPY requirements.txt /home/worker/requirements.txt
RUN pip install --upgrade pip
RUN pip install -r /home/worker/requirements.txt

# copy the rest of the app
COPY . /home/worker

WORKDIR /home/worker
