FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive     

# Add deadsnakes PPA for Python 3.12
RUN apt-get update && apt-get install -y software-properties-common 

# Install required packages including git, python3.12, pip, wget, and unzip
RUN apt-get update && apt-get install -y \
    git \
    wget \
    unzip
COPY requirements.txt .
RUN pip install -r requirements.txt

# Create symlink for python3 to point to python3.12
RUN mkdir -p $(python3 -c "import site; print(site.getsitepackages()[0])")
# Download, unzip, and copy the extracted contents to Python's site-packages directory
RUN wget "https://s3.amazonaws.com/ifcopenshell-builds/ifcopenshell-python-312-v0.8.3-bb329af-linux64.zip" -O /tmp/ifcopenshell.zip && \
    unzip /tmp/ifcopenshell.zip -d /tmp/ifcopenshell && \
    cp -r /tmp/ifcopenshell/* $(python3 -c "import site; print(site.getsitepackages()[0])")

WORKDIR /usr/src/app
