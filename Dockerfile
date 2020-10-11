FROM python:3.6.6

RUN groupadd -r keeper && useradd -d /home/keeper -m --no-log-init -r -g keeper keeper && \
    apt-get -y update && \
    apt-get -y install jq bc && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY . /opt/keeper/settlement-keeper/

RUN cd /opt/keeper/settlement-keeper && \
    git submodule update --init --recursive && \
    pip3 install virtualenv && \
    ./install.sh

WORKDIR /opt/keeper/settlement-keeper

CMD ["./run-settlement-keeper.sh"]
