FROM python:3.12
LABEL maintainer="IETF Tools Team <tools-discuss@ietf.org>"

ENV DEBIAN_FRONTEND=noninteractive

# Update system packages
RUN apt-get update \
    && apt-get -qy upgrade \
    && apt-get -y install --no-install-recommends apt-utils ca-certificates curl dialog gnupg lsb-release 2>&1

# Add Node.js Source
RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list

# Add PostgreSQL Source
RUN echo "deb https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | apt-key add -

# Install the packages we need
RUN apt-get update --fix-missing && apt-get install -qy \
	bash \
	build-essential \
	gcc \
	git \
	jq \
	less \
	make \
	nano \
	netcat-openbsd \
	nodejs \
	postgresql-client-14 \
	unzip \
	wget \
	zsh

# Get rid of installation files we don't need in the image, to reduce size
RUN apt-get autoremove -y && apt-get clean -y && rm -rf /var/lib/apt/lists/*

# avoid million NPM install messages
ENV npm_config_loglevel warn
# allow installing when the main user is root
ENV npm_config_unsafe_perm true
# disable NPM funding messages
ENV npm_config_fund false

# Colorize the bash shell
RUN sed -i 's/#force_color_prompt=/force_color_prompt=/' /root/.bashrc

# Fetch wait-for utility
ADD https://raw.githubusercontent.com/eficode/wait-for/v2.2.4/wait-for /usr/local/bin/
RUN chmod +rx /usr/local/bin/wait-for

# Create workspace
RUN mkdir -p /workspace
WORKDIR /workspace

ENV DEBIAN_FRONTEND=noninteractive

# Install needed packages and setup non-root user.
ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=$USER_UID
COPY docker/scripts/app-setup.sh /tmp/library-scripts/docker-setup.sh
RUN sed -i 's/\r$//' /tmp/library-scripts/docker-setup.sh && chmod +x /tmp/library-scripts/docker-setup.sh
RUN bash /tmp/library-scripts/docker-setup.sh "${USERNAME}" "${USER_UID}" "${USER_GID}"

COPY docker/configs/.tmux.conf /home/dev/.tmux.conf

# Setup nginx
COPY docker/configs/nginx-proxy.conf /etc/nginx/sites-available/default
COPY docker/configs/nginx-502.html /var/www/html/502.html

# Enable manage.py completion for bash/zsh
RUN mkdir -p /usr/local/share/django/extras
RUN wget -O /usr/local/share/django/extras/django_bash_completion \
    https://github.com/django/django/raw/main/extras/django_bash_completion
RUN echo "source /usr/local/share/django/extras/django_bash_completion" >> /home/$USERNAME/.profile

# Copy the startup file
COPY docker/scripts/app-init.sh /docker-init.sh
COPY docker/scripts/app-start.sh /docker-start.sh
RUN sed -i 's/\r$//' /docker-init.sh && chmod +x /docker-init.sh
RUN sed -i 's/\r$//' /docker-start.sh && chmod +x /docker-start.sh

# Fix user UID / GID to match host
RUN groupmod --gid $USER_GID $USERNAME \
    && usermod --uid $USER_UID --gid $USER_GID $USERNAME \
    && chown -R $USER_UID:$USER_GID /home/$USERNAME \
    || exit 0

# Switch to local dev user
USER dev:dev

# Install pylint dependencies
RUN pip3 --disable-pip-version-check --no-cache-dir install --user --no-warn-script-location pylint pylint-django
