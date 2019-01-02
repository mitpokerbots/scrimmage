FROM python:2.7-slim

# copy only the files needed for pip install
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip
RUN pip install -r /app/requirements.txt

# copy the rest of the app
COPY . /app

WORKDIR /app
