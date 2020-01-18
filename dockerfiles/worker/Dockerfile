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
RUN pip install toposort
RUN pip install torch torchvision

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
# Install the Just command runner
RUN cargo install just -q
RUN cargo install cargo-build-deps -q

# Take steps needed to prebuild only declared dependencies and work with non-Rust bots
# So allow for failure (because cargo commands require a Cargo.toml file to work)
# and non Rust projects don't have that

# Only build specific packages, that are specified as dependencies
# We don't need to actually pull the package, as the package is expected to run offline anyways
# So the package must include all source code it actually needs to build
# Turns out this is a non-trivial problem. Just use a package.

# Only build dependencies of the currently selected package if it is a Rust package
# But ignore any errors
RUN cargo build-deps || true
RUN cargo build-deps --release || true
