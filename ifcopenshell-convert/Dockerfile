FROM python:3.11-slim
ENV DEBIAN_FRONTEND=noninteractive

# Add deadsnakes PPA for Python 3.12
RUN apt-get update && apt-get install -y software-properties-common

# Install required packages including git, python3.12, pip, wget, and unzip
RUN apt-get update && apt-get install -y \
    git \
    wget \
    xz-utils \
    unzip \
    libxrender1 \
    libxi6 \
    libxkbcommon0 \
    libxss1 \
    libxtst6 \
    libxrandr2 \
    libasound2 \
    libpangocairo-1.0-0 \
    libatk1.0-0 \
    libcairo-gobject2 \
    libgtk-3-0 \
    libgdk-pixbuf2.0-0 \
    libxxf86vm1

COPY requirements.txt .
RUN pip install -r requirements.txt

ENV BLENDER_VERSION=4.3.0
RUN wget -q https://download.blender.org/release/Blender${BLENDER_VERSION%.*}/blender-${BLENDER_VERSION}-linux-x64.tar.xz -O /tmp/blender.tar.xz && \
    mkdir -p /opt/blender && \
    tar -xf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 && \
    ln -s /opt/blender/blender /usr/local/bin/blender && \
    rm /tmp/blender.tar.xz

RUN /opt/blender/4.3/python/bin/python3.11 -m ensurepip
RUN /opt/blender/4.3/python/bin/python3.11 -m pip install ifcopenshell bpy bcf-client bsdd ifcclash  ifc5d ifc4d ifcfm ifccsv ifcdiff\
 ifcpatch lxml platformdirs svgwrite pystache natsort pillow ifctester networkx bidict tzfpy  pyradiance 

RUN apt-get update && apt-get install -y libsm6 libgl1-mesa-glx libgomp1 build-essential


RUN wget \
    https://extensions.blender.org/media/files/8e/8e10884661d35aa1cb126dee7d7712eeef4838403e18a440750a33ba433661d8.zip?filename=add-on-bonsai-v0.8.2-linux-x64.zip \
    -O ./bonsai.zip 

COPY lib/bonsai /opt/blender/4.3/python/lib/python3.11/site-packages/bonsai
COPY lib/bpypolyskel /opt/blender/4.3/python/lib/python3.11/site-packages/bpypolyskel
COPY lib/socketio /opt/blender/4.3/python/lib/python3.11/site-packages/socketio
COPY lib/engineio /opt/blender/4.3/python/lib/python3.11/site-packages/engineio


# RUN blender -b --python-expr "import bpy; bpy.ops.preferences.addon_enable(module='bonsai'); bpy.ops.wm.save_userpref()"

WORKDIR /usr/src/app



