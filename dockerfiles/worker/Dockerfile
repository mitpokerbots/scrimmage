FROM python:3.7

RUN useradd -ms /bin/bash worker

# copy only the files needed for pip install
RUN pip install --upgrade pip
RUN pip install Cython

COPY requirements.txt /home/worker/requirements.txt
RUN pip install -r /home/worker/requirements.txt

COPY requirements-worker.txt /home/worker/requirements-worker.txt
RUN pip install -r /home/worker/requirements-worker.txt

# User requested
RUN pip install treys

ENV DEBIAN_FRONTEND noninteractive

RUN apt-get update -y
RUN apt-get upgrade -y
RUN apt-get install -yq default-jdk
RUN apt-get install -yq gcc g++ scons autoconf libtool git libboost-all-dev libc6-dev
RUN apt-get install -yq nano

# User requested
RUN apt-get -y install cmake

COPY ./deps/BayesElo /home/worker/BayesElo
WORKDIR /home/worker/BayesElo
RUN make
RUN cp /home/worker/BayesElo/bayeselo /usr/bin/bayeselo
RUN rm -rf /home/worker/BayesElo

USER worker

# copy the rest of the app
COPY ./scrimmage /home/worker/scrimmage
COPY ./deps/engine/engine.py /home/worker/engine/engine.py

WORKDIR /home/worker

# User requested

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/home/worker/.cargo/bin:${PATH}"
RUN rustup install nightly
