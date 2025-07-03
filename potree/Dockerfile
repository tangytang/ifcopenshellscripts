# Use an official Ubuntu as a parent image
FROM ubuntu:20.04
ARG DEBIAN_FRONTEND=noninteractive


# Install dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    libproj-dev \
    libboost-all-dev \
    libtbb-dev \ 
    python3-pip \
    && rm -rf /var/lib/apt/lists/*


WORKDIR /usr/src/app
# Clone Potree Converter
RUN git clone --recursive https://github.com/potree/PotreeConverter.git /usr/src/app/PotreeConverter

# Build Potree Converter
WORKDIR /usr/src/app/PotreeConverter
RUN mkdir build && cd build \
    && cmake ../ \
    && make

WORKDIR /usr/src/app


# Add Potree Converter to PATH
ENV PATH="/usr/src/app/PotreeConverter/build:${PATH}"


COPY requirements.txt requirements.txt
COPY . .


RUN pip install -r requirements.txt
