# MobaXterm Quick Start Guide

This guide provides a concise overview of MobaXterm configuration and features based on the project documentation.

## 🛠️ Core Configuration
*   **Persistence:** To ensure your files and programs survive a restart, configure a **Persistent home directory** (`/home/mobaxterm`) and a **Persistent root (/) directory**. This is especially critical if you use the `MobApt` tool.
*   **Configuration File:** Your settings and sessions are stored in `MobaXterm.ini`. 
    *   *Installed edition:* `MyDocuments\MobaXterm`
    *   *Portable edition:* Same folder as the executable.
    *   *Custom Path:* Start MobaXterm with the `-i` flag to specify a config location: `MobaXterm.exe -i "D:\Data\MobaXterm.ini"`.

## 🖥️ Terminal Customization
*   **Windows Integration:** Enable **"Use Windows PATH environment"** to run Windows commands (like `ipconfig`) directly from the MobaXterm shell.
*   **Efficiency:** 
    *   **Right-Click Paste:** Enable "Paste using right-click" for faster workflow (use `Ctrl+Right-click` for the context menu).
    *   **Activity Tracking:** A blue dot on the tab icon indicates active terminal activity.
*   **Visuals:** You can customize terminal colors, fonts, and syntax highlighting definitions in the settings.

## 🔐 Connectivity & Security
*   **Password Management:** MobaXterm can store passwords and protect them with a **Master Password**.
*   **SSH Client:** Based on PuTTY, MobaXterm includes a secure SSH client with SFTP and SCP.
    *   **Passwordless SSH:** 
        *   Generate a key: `ssh-keygen -t rsa -N '' -q -f ~/.ssh/id_rsa` (or use **MobaKeyGen** in the Tools menu).
        *   Deploy key: `scp .ssh/id_rsa.pub user@server:.ssh/authorized_keys`.
    *   **SSH Session Settings:** You can configure X11-Forwarding, Compression, and the SSH-browser type per session.
    *   **Troubleshooting:**
        *   *Connection Reset:* Try the "Workaround for connection reset by peer" in SSH settings, or disable Compression/SSH-browser.
        *   *Freezing/Drops:* Enable **"SSH keepalive"** in Settings $\rightarrow$ Configuration $\rightarrow$ SSH to prevent NAT/firewall timeouts.
        *   *Unprotected Key Warning:* Ensure the `/home/mobaxterm/.ssh` folder has correct group permissions.
*   **Jump Hosts:** Use the Network settings to connect through an SSH gateway (jump host).

## 🚀 Advanced Features
*   **X11 Server:** Configure the rendering engine and keyboard language in the X11 tab. Use "Unix-compatible keyboard" if you need the erase key to perform `^H` instead of `^?`.
*   **Automation:** You can execute shell scripts at startup using either command-line parameters or bookmarks.
*   **Collaboration:** Use the **Shared Sessions** feature to point to session files shared by team members.

## ⌨️ Quick Tips
*   **Right-Click Menu:** You can add "Start MobaXterm here" to the Windows Explorer right-click menu.
*   **Logging:** Enable "Log terminal output" to save all activity to a text file for later analysis.
*   **Safety:** Enable "Warn before pasting multiple lines" to prevent accidental execution of large blocks of code.
